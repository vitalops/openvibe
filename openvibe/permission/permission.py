"""Permission system.

Every tool call is checked against an ordered list of rules. The first
matching rule wins.  If no rule matches, the default is ``ask``.

Rules are evaluated with ``fnmatch`` glob patterns, so patterns like
``*.env``, ``/etc/**``, ``bash`` are all valid.

Rule actions
------------
- ``allow`` — proceed immediately.
- ``deny``  — raise ``PermissionDenied``.
- ``ask``   — suspend the call and emit a ``PermissionRequestedEvent`` on the
              bus. The caller (HTTP handler or CLI) must call ``reply()`` to
              resume or reject.
"""

from __future__ import annotations

import asyncio
import fnmatch
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from openvibe.bus import Event, EventBus
from openvibe.config import PermissionAction

if TYPE_CHECKING:
    from openvibe.db import Database


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PermissionDenied(Exception):
    """Raised when a ``deny`` rule matches a tool call."""

    def __init__(self, tool: str, pattern: str | None = None) -> None:
        self.tool = tool
        self.pattern = pattern
        super().__init__(f"Permission denied for tool '{tool}'")


class PermissionRejected(Exception):
    """Raised when the user explicitly rejects an ``ask`` prompt."""

    def __init__(self, tool: str) -> None:
        self.tool = tool
        super().__init__(f"User rejected permission for tool '{tool}'")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@dataclass
class PermissionRequestedEvent(Event):
    request_id: str = ""
    tool: str = ""
    description: str = ""        # human-readable description of what the tool will do


@dataclass
class PermissionRepliedEvent(Event):
    request_id: str = ""
    decision: PermissionAction = PermissionAction.DENY
    remember: bool = False  # if True, store as a project-level allow rule


# ---------------------------------------------------------------------------
# Rule model
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    tool: str                               # exact name or glob
    action: PermissionAction
    pattern: str | None = None              # optional path/argument glob


def _matches(rule: Rule, tool: str, argument: str | None = None) -> bool:
    """Return True if *rule* applies to this (tool, argument) combination."""
    if not fnmatch.fnmatch(tool, rule.tool):
        return False
    if rule.pattern and argument:
        return fnmatch.fnmatch(argument, rule.pattern)
    return True


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class PermissionService:
    """Evaluate permission rules and manage ask/reply lifecycle."""

    def __init__(self, db: "Database", bus: EventBus) -> None:
        self._db = db
        self._bus = bus
        # in-flight ask requests: request_id → Future
        self._pending: dict[str, asyncio.Future[PermissionAction]] = {}

    # ------------------------------------------------------------------
    # Rule loading
    # ------------------------------------------------------------------

    def load_rules(self, project_id: str) -> list[Rule]:
        """Load stored rules for *project_id* from the database."""
        rows = self._db.fetchall(
            "SELECT tool, action, pattern FROM permissions WHERE project_id = ? ORDER BY rowid",
            (project_id,),
        )
        return [Rule(tool=r["tool"], action=r["action"], pattern=r.get("pattern")) for r in rows]

    def save_rule(self, project_id: str, rule: Rule) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            "INSERT INTO permissions (id, project_id, tool, pattern, action, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (f"perm_{uuid.uuid4().hex[:12]}", project_id, rule.tool, rule.pattern, rule.action, now),
        )

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    async def check(
        self,
        tool: str,
        argument: str | None = None,
        rules: list[Rule] | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        description: str = "",
    ) -> None:
        """Check permission for a tool call.

        Raises ``PermissionDenied`` or ``PermissionRejected`` on failure.
        Returns normally when the call is permitted.

        *rules* can be passed directly (e.g. agent-level rules); if omitted
        and *project_id* is given, rules are loaded from the database.
        """
        effective_rules: list[Rule] = list(rules or [])
        if project_id and not rules:
            effective_rules = self.load_rules(project_id)

        for rule in effective_rules:
            if _matches(rule, tool, argument):
                match rule.action:
                    case PermissionAction.ALLOW:
                        return
                    case PermissionAction.DENY:
                        raise PermissionDenied(tool, rule.pattern)
                    case PermissionAction.ASK:
                        await self._ask(
                            tool=tool,
                            description=description,
                            session_id=session_id,
                            project_id=project_id,
                        )
                        return

        # No rule matched → ask by default
        await self._ask(tool=tool, description=description, session_id=session_id)

    async def _ask(
        self,
        tool: str,
        description: str,
        session_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        """Suspend until the user replies via ``reply()``."""
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[PermissionAction] = loop.create_future()
        self._pending[request_id] = future

        await self._bus.publish(
            PermissionRequestedEvent(
                session_id=session_id,
                request_id=request_id,
                tool=tool,
                description=description,
            )
        )

        try:
            decision = await future
        finally:
            self._pending.pop(request_id, None)

        if decision == PermissionAction.DENY:
            raise PermissionRejected(tool)

    def reply(
        self,
        request_id: str,
        decision: PermissionAction,
        remember: bool = False,
        project_id: str | None = None,
        tool: str | None = None,
    ) -> None:
        """Respond to a pending ``ask``.

        If *remember* is True and *project_id* + *tool* are provided, the
        decision is persisted as a project-level rule.
        """
        future = self._pending.get(request_id)
        if future and not future.done():
            future.set_result(decision)

        if remember and decision == PermissionAction.ALLOW and project_id and tool:
            self.save_rule(project_id, Rule(tool=tool, action=PermissionAction.ALLOW))

        asyncio.get_running_loop().create_task(
            self._bus.publish(
                PermissionRepliedEvent(
                    request_id=request_id,
                    decision=decision,
                    remember=remember,
                )
            )
        )
