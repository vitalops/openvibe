"""Session and message persistence.

Thin data-access layer; all business logic lives in the processor.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from openvibe.config import MessageRole
from openvibe.session.models import (MessageInfo, MessagePart, SessionInfo,
                                     now_iso)

if TYPE_CHECKING:
    from openvibe.db import Database


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


def create(
    db: "Database",
    project_id: str,
    directory: str,
    parent_id: str | None = None,
    title: str | None = None,
) -> SessionInfo:
    session_id = f"ses_{uuid.uuid4().hex}"
    slug = _slug(session_id)
    now = now_iso()
    db.execute(
        "INSERT INTO sessions "
        "(id, project_id, slug, title, parent_id, directory, version, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,1,?,?)",
        (session_id, project_id, slug, title, parent_id, directory, now, now),
    )
    return get(db, session_id)  # type: ignore[return-value]


def get(db: "Database", session_id: str) -> SessionInfo | None:
    row = db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
    return SessionInfo(**row) if row else None


def list_sessions(db: "Database", project_id: str) -> list[SessionInfo]:
    rows = db.fetchall(
        "SELECT * FROM sessions WHERE project_id = ? AND archived_at IS NULL "
        "ORDER BY updated_at DESC",
        (project_id,),
    )
    return [SessionInfo(**r) for r in rows]


def update_title(db: "Database", session_id: str, title: str) -> None:
    db.execute(
        "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
        (title, now_iso(), session_id),
    )


def update_cost(
    db: "Database",
    session_id: str,
    *,
    cost: float,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> None:
    db.execute(
        "UPDATE sessions SET "
        "cost = cost + ?, input_tokens = input_tokens + ?, output_tokens = output_tokens + ?, "
        "cache_read_tokens = cache_read_tokens + ?, cache_write_tokens = cache_write_tokens + ?, "
        "updated_at = ? WHERE id = ?",
        (
            cost,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            now_iso(),
            session_id,
        ),
    )


def update_config(db: "Database", session_id: str, config_json: str | None) -> None:
    """Persist session-level config overrides (JSON string or None to clear)."""
    db.execute(
        "UPDATE sessions SET config_json = ?, updated_at = ? WHERE id = ?",
        (config_json, now_iso(), session_id),
    )


def archive(db: "Database", session_id: str) -> None:
    db.execute(
        "UPDATE sessions SET archived_at = ?, updated_at = ? WHERE id = ?",
        (now_iso(), now_iso(), session_id),
    )


def delete(db: "Database", session_id: str) -> None:
    db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def fork(
    db: "Database",
    session_id: str,
    up_to_message_id: str | None = None,
) -> SessionInfo:
    """Create a copy of this session (optionally truncated to a message)."""
    original = get(db, session_id)
    if not original:
        raise ValueError(f"Session {session_id} not found")

    new_session = create(
        db, original.project_id, original.directory, parent_id=session_id
    )

    messages = list_messages(db, session_id)
    for msg in messages:
        if up_to_message_id and msg.id == up_to_message_id:
            break
        copy_message(db, msg, new_session.id)

    return new_session


# ---------------------------------------------------------------------------
# Message CRUD
# ---------------------------------------------------------------------------


def add_message(
    db: "Database",
    session_id: str,
    role: MessageRole,
    parts: list[MessagePart] | None = None,
) -> MessageInfo:
    msg_id = f"msg_{uuid.uuid4().hex}"
    now = now_iso()

    # next position
    row = db.fetchone(
        "SELECT MAX(position) AS pos FROM messages WHERE session_id = ?", (session_id,)
    )
    position = (row["pos"] or 0) + 1 if row else 1

    db.execute(
        "INSERT INTO messages (id, session_id, role, position, created_at) VALUES (?,?,?,?,?)",
        (msg_id, session_id, role, position, now),
    )
    msg = MessageInfo(
        id=msg_id, session_id=session_id, role=role, position=position, created_at=now
    )

    if parts:
        for i, part in enumerate(parts):
            _upsert_part(db, msg_id, i, part)
        msg.parts = list(parts)

    db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
    return msg


def list_messages(db: "Database", session_id: str) -> list[MessageInfo]:
    msg_rows = db.fetchall(
        "SELECT * FROM messages WHERE session_id = ? ORDER BY position", (session_id,)
    )
    messages = []
    for row in msg_rows:
        parts = _load_parts(db, row["id"])
        msg = MessageInfo(**row, parts=parts)
        messages.append(msg)
    return messages


def upsert_part(db: "Database", message_id: str, index: int, part: MessagePart) -> None:
    """Persist (insert or replace) a single message part."""
    _upsert_part(db, message_id, index, part)


def copy_message(db: "Database", msg: MessageInfo, new_session_id: str) -> MessageInfo:
    """Duplicate a message into a different session."""
    return add_message(db, new_session_id, msg.role, msg.parts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _upsert_part(
    db: "Database", message_id: str, index: int, part: MessagePart
) -> None:
    part_id = f"part_{message_id}_{index}"
    db.execute(
        "INSERT OR REPLACE INTO parts (id, message_id, type, position, data) VALUES (?,?,?,?,?)",
        (part_id, message_id, part.type, index, part.model_dump_json()),  # type: ignore[union-attr]
    )


def _load_parts(db: "Database", message_id: str) -> list[MessagePart]:
    from pydantic import TypeAdapter

    _adapter: TypeAdapter[MessagePart] = TypeAdapter(MessagePart)  # type: ignore[type-arg]
    rows = db.fetchall(
        "SELECT data FROM parts WHERE message_id = ? ORDER BY position", (message_id,)
    )
    parts: list[MessagePart] = []
    for row in rows:
        try:
            parts.append(_adapter.validate_json(row["data"]))
        except Exception:
            pass  # skip corrupted parts rather than crashing
    return parts


def _slug(session_id: str) -> str:
    # Use the last 8 hex chars as a short slug
    return session_id.split("_", 1)[-1][:8]
