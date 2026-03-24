"""Database abstraction layer.

The default backend uses raw SQLite via Python's stdlib ``sqlite3`` module.
All persistence in openvibe routes through the ``Database`` protocol, so the
underlying engine can be replaced without touching application code.

Implementing a custom backend
------------------------------
Create a class that satisfies the ``Database`` protocol::

    class MyDatabase:
        def execute(self, sql: str, params: tuple = ()) -> list[Row]: ...
        def executemany(self, sql: str, params: list[tuple]) -> None: ...
        def fetchone(self, sql: str, params: tuple = ()) -> Row | None: ...
        def fetchall(self, sql: str, params: tuple = ()) -> list[Row]: ...
        def transaction(self) -> ContextManager[None]: ...
        def migrate(self) -> None: ...
        def close(self) -> None: ...

Then inject it::

    app = create_app(db=MyDatabase())

All SQL uses ``?`` placeholders (SQLite style). Custom backends must translate
as needed.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

Row = dict[str, Any]


@runtime_checkable
class Database(Protocol):
    """Protocol every database backend must satisfy."""

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[Row]:
        """Run a DML statement and return produced rows (e.g. via RETURNING)."""
        ...

    def executemany(self, sql: str, params: list[tuple[Any, ...]]) -> None:
        """Run a DML statement once per parameter set."""
        ...

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> Row | None:
        """Return the first row of a SELECT, or None."""
        ...

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[Row]:
        """Return all rows of a SELECT."""
        ...

    @contextmanager
    def transaction(self) -> Generator[None, None, None]:
        """Commit on exit, roll back on exception."""
        ...

    def migrate(self) -> None:
        """Apply any pending schema migrations."""
        ...

    def close(self) -> None:
        """Release all connections."""
        ...


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects(id),
    slug              TEXT NOT NULL,
    title             TEXT,
    parent_id         TEXT REFERENCES sessions(id),
    directory         TEXT NOT NULL,
    version           INTEGER NOT NULL DEFAULT 1,
    cost              REAL NOT NULL DEFAULT 0,
    input_tokens      INTEGER NOT NULL DEFAULT 0,
    output_tokens     INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    archived_at       TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK(role IN ('user','assistant','system','error','permission')),
    position    INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parts (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    type        TEXT NOT NULL,
    position    INTEGER NOT NULL,
    data        TEXT NOT NULL  -- JSON blob
);

CREATE TABLE IF NOT EXISTS todos (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','in_progress','completed','cancelled')),
    priority    TEXT NOT NULL DEFAULT 'medium'
        CHECK(priority IN ('low','medium','high')),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permissions (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tool        TEXT NOT NULL,
    pattern     TEXT,
    action      TEXT NOT NULL CHECK(action IN ('allow','deny','ask')),
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, position);
CREATE INDEX IF NOT EXISTS idx_parts_message    ON parts(message_id, position);
CREATE INDEX IF NOT EXISTS idx_todos_session    ON todos(session_id);
CREATE INDEX IF NOT EXISTS idx_permissions_proj ON permissions(project_id);
"""


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------


class SQLiteDatabase:
    """Raw SQLite implementation of ``Database``.

    Thread-safe via thread-local connections. Uses WAL journal mode for
    better concurrent read performance and reduced lock contention.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._local = threading.local()
        # Prime PRAGMA settings on the first connection
        conn = self._conn
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-65536")  # 64 MB page cache
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()

    @property
    def _conn(self) -> sqlite3.Connection:
        if not getattr(self._local, "conn", None):
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn  # type: ignore[return-value]

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[Row]:
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return [dict(r) for r in (cur.fetchall() or [])]

    def executemany(self, sql: str, params: list[tuple[Any, ...]]) -> None:
        self._conn.executemany(sql, params)
        self._conn.commit()

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> Row | None:
        row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[Row]:
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    @contextmanager
    def transaction(self) -> Generator[None, None, None]:
        try:
            yield
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def migrate(self) -> None:
        """Apply bundled schema (idempotent — uses CREATE IF NOT EXISTS)."""
        for stmt in _SCHEMA.split(";"):
            s = stmt.strip()
            if s:
                self._conn.execute(s)
        # Incremental migrations for columns added after initial schema.
        self._add_column_if_missing("sessions", "config_json", "TEXT")
        self._conn.commit()

    def _add_column_if_missing(
        self, table: str, column: str, col_type: str
    ) -> None:
        """Idempotent ALTER TABLE ADD COLUMN."""
        cols = {
            row["name"]
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in cols:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    def close(self) -> None:
        if conn := getattr(self._local, "conn", None):
            conn.close()
            self._local.conn = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def default_db_path() -> Path:
    """Return ``~/.openvibe/openvibe.db`` (honouring ``OPENVIBE_CHANNEL``)."""
    import os

    channel = os.environ.get("OPENVIBE_CHANNEL", "")
    suffix = f".{channel}" if channel else ""
    return Path.home() / ".openvibe" / f"openvibe{suffix}.db"


def create_database(path: Path | None = None) -> SQLiteDatabase:
    """Create, migrate, and return a SQLite database at *path*."""
    db = SQLiteDatabase(path or default_db_path())
    db.migrate()
    return db
