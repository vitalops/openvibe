"""Session-scoped computer-use sandbox.

The sandbox tracks every action taken against the screen, enforces an
optional application allow-list, and maintains a full audit log that can
be exported for reproducibility / compliance purposes.

Usage::

    from openvibe.computer.sandbox import get_sandbox, ActionType

    sandbox = get_sandbox(session_id)
    await sandbox.record_action(ActionType.SCREENSHOT, params={}, result="ok")
    log = sandbox.export_audit_log()
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Action catalogue
# ---------------------------------------------------------------------------


class ActionType(str, Enum):
    """All actions the computer-use subsystem can perform."""

    SCREENSHOT = "screenshot"
    MOUSE_CLICK = "mouse_click"
    MOUSE_MOVE = "mouse_move"
    MOUSE_SCROLL = "mouse_scroll"
    MOUSE_DRAG = "mouse_drag"
    KEYBOARD_TYPE = "keyboard_type"
    KEYBOARD_PRESS = "keyboard_press"
    KEYBOARD_HOTKEY = "keyboard_hotkey"
    APP_OPEN = "app_open"
    APP_CLOSE = "app_close"
    APP_FOCUS = "app_focus"
    APP_LIST = "app_list"


# ---------------------------------------------------------------------------
# Audit entry
# ---------------------------------------------------------------------------


@dataclass
class AuditEntry:
    """A single recorded action in the audit log."""

    id: str
    timestamp: float
    action_type: ActionType
    params: dict[str, Any]
    session_id: str
    result: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "action": self.action_type.value,
            "params": self.params,
            "session_id": self.session_id,
            "result": self.result,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


@dataclass
class ComputerSandbox:
    """Per-session sandbox that gates and records computer-use actions.

    Attributes
    ----------
    session_id:
        Owning session — used to correlate audit entries.
    allowed_apps:
        If non-empty, only these application names may be opened/focused.
        Comparisons are case-insensitive substring matches.
    screen_region:
        Optional ``(x, y, width, height)`` bounding box.  When set,
        screenshot and click coordinates are validated to stay inside.
    audit_log:
        Ordered list of every action taken this session.
    """

    session_id: str
    allowed_apps: list[str] = field(default_factory=list)
    # (x, y, width, height) or None for "full screen"
    screen_region: tuple[int, int, int, int] | None = None
    audit_log: list[AuditEntry] = field(default_factory=list)
    # Last captured screenshot PNG bytes — used for automatic change detection.
    last_screenshot: bytes | None = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def is_app_allowed(self, app_name: str) -> bool:
        """Return True when *app_name* passes the allow-list check."""
        if not self.allowed_apps:
            return True  # no restrictions
        name_lower = app_name.lower()
        return any(allowed.lower() in name_lower for allowed in self.allowed_apps)

    def is_coordinate_allowed(self, x: int, y: int) -> bool:
        """Return True when *(x, y)* is within the permitted screen region."""
        if self.screen_region is None:
            return True
        rx, ry, rw, rh = self.screen_region
        return rx <= x <= rx + rw and ry <= y <= ry + rh

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    async def record_action(
        self,
        action_type: ActionType,
        params: dict[str, Any],
        result: str | None = None,
        error: str | None = None,
    ) -> AuditEntry:
        """Append an entry to the audit log and return it."""
        entry = AuditEntry(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            action_type=action_type,
            params=params,
            session_id=self.session_id,
            result=result,
            error=error,
        )
        async with self._lock:
            self.audit_log.append(entry)
        return entry

    def export_audit_log(self) -> list[dict[str, Any]]:
        """Return the full audit log as a list of plain dicts."""
        return [e.to_dict() for e in self.audit_log]

    def summary(self) -> str:
        """Return a human-readable summary line."""
        counts: dict[str, int] = {}
        for e in self.audit_log:
            counts[e.action_type.value] = counts.get(e.action_type.value, 0) + 1
        parts = ", ".join(f"{v}× {k}" for k, v in sorted(counts.items()))
        return f"session={self.session_id[:12]}… | {len(self.audit_log)} actions: {parts or 'none'}"


# ---------------------------------------------------------------------------
# Global registry keyed by session_id
# ---------------------------------------------------------------------------

_sandboxes: dict[str, ComputerSandbox] = {}


def get_sandbox(session_id: str) -> ComputerSandbox:
    """Return (creating if needed) the sandbox for *session_id*."""
    if session_id not in _sandboxes:
        _sandboxes[session_id] = ComputerSandbox(session_id=session_id)
    return _sandboxes[session_id]


def clear_sandbox(session_id: str) -> None:
    """Discard the sandbox for *session_id*."""
    _sandboxes.pop(session_id, None)
