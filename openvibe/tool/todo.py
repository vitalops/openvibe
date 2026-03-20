"""Todo tools — manage a session-scoped task list.

Two complementary tools:
- ``TodoWriteTool`` — create / update / delete todos
- ``TodoReadTool``  — list all todos for the current session

The task list helps the agent track multi-step work, communicate progress,
and avoid forgetting outstanding items.
"""

from __future__ import annotations

import uuid
from typing import Literal, TYPE_CHECKING

from pydantic import Field

from openvibe.session.models import now_iso
from openvibe.tool.base import Tool, ToolContext, ToolResult

if TYPE_CHECKING:
    from openvibe.db import Database


# ---------------------------------------------------------------------------
# TodoWriteTool
# ---------------------------------------------------------------------------

class TodoWriteTool(Tool):
    name = "todo_write"
    description = (
        "Create, update, or delete todos for the current session. "
        "Use this to track tasks, mark them complete, or remove them. "
        "Always read the current list before writing to avoid duplicates."
    )

    class Params(Tool.Params):
        todos: list["_TodoItem"] = Field(
            description="Full replacement list of todos. "
            "Pass the complete list (including unchanged items); "
            "items not included are deleted.",
        )

    async def execute(self, ctx: ToolContext, params: "TodoWriteTool.Params") -> ToolResult:
        from openvibe.tool.todo import _sync_todos  # avoid circular at module level
        count = _sync_todos(ctx, params.todos)
        return ToolResult(
            title="Updated todo list",
            output=f"Todo list updated ({count} item(s)).",
        )


class _TodoItem(Tool.Params):
    id: str | None = Field(
        default=None,
        description="Existing todo ID to update. Omit to create a new item.",
    )
    content: str = Field(description="Todo description.")
    status: Literal["pending", "in_progress", "completed", "cancelled"] = "pending"
    priority: Literal["low", "medium", "high"] = "medium"


def _sync_todos(ctx: ToolContext, items: list[_TodoItem]) -> int:
    """Replace the session's todo list with *items* in the DB.

    This deliberately avoids injecting the DB directly into Tool instances
    (which would complicate the interface). Instead, the session processor
    injects a reference to the DB into the ToolContext via a monkey-patch
    on first use.  See processor.py for how ``_db`` is attached.
    """
    db = getattr(ctx, "_db", None)
    if db is None:
        return 0

    # Delete existing todos for this session, then insert the new list
    db.execute("DELETE FROM todos WHERE session_id = ?", (ctx.session_id,))
    now = now_iso()
    for item in items:
        todo_id = item.id or f"todo_{uuid.uuid4().hex[:12]}"
        db.execute(
            "INSERT INTO todos (id, session_id, content, status, priority, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (todo_id, ctx.session_id, item.content, item.status, item.priority, now, now),
        )
    return len(items)


# ---------------------------------------------------------------------------
# TodoReadTool
# ---------------------------------------------------------------------------

class TodoReadTool(Tool):
    name = "todo_read"
    description = (
        "Read the current todo list for this session. "
        "Call this before todo_write to see existing items."
    )

    class Params(Tool.Params):
        pass

    async def execute(self, ctx: ToolContext, params: "TodoReadTool.Params") -> ToolResult:
        db = getattr(ctx, "_db", None)
        if db is None:
            return ToolResult(title="Todos", output="(no todo storage available)")

        rows = db.fetchall(
            "SELECT id, content, status, priority FROM todos "
            "WHERE session_id = ? ORDER BY rowid",
            (ctx.session_id,),
        )

        if not rows:
            return ToolResult(title="Todos", output="No todos yet.")

        lines = []
        for row in rows:
            status_icon = {"pending": "○", "in_progress": "◎", "completed": "✓", "cancelled": "✗"}.get(
                row["status"], "?"
            )
            lines.append(f"[{status_icon}] ({row['priority']}) {row['content']}  id={row['id']}")

        return ToolResult(
            title=f"Todos ({len(rows)} items)",
            output="\n".join(lines),
        )
