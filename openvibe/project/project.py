"""Project detection and workspace management.

A "project" is a directory tracked by git (or any VCS). openvibe uses the
git root as the canonical project path and derives a stable ID from it.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openvibe.db import Database


@dataclass
class ProjectInfo:
    id: str
    path: str        # absolute path to the project root
    created_at: str
    updated_at: str


def _project_id(path: Path) -> str:
    """Derive a stable, short project ID from the absolute path."""
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:16]
    return f"proj_{digest}"


def find_git_root(start: Path) -> Path | None:
    """Walk up from *start* until a ``.git`` directory is found."""
    current = start.resolve()
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def get_or_create(db: "Database", directory: Path) -> ProjectInfo:
    """Return the project for *directory*, creating it in the DB if needed.

    Uses the git root when available; falls back to *directory* itself.
    """
    root = find_git_root(directory) or directory.resolve()
    project_id = _project_id(root)

    row = db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    if row:
        return ProjectInfo(**row)

    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO projects (id, path, created_at, updated_at) VALUES (?,?,?,?)",
        (project_id, str(root), now, now),
    )
    return ProjectInfo(id=project_id, path=str(root), created_at=now, updated_at=now)


def get(db: "Database", project_id: str) -> ProjectInfo | None:
    row = db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    return ProjectInfo(**row) if row else None


def is_inside_project(project: ProjectInfo, path: Path) -> bool:
    """Return True if *path* is under the project root."""
    try:
        path.resolve().relative_to(project.path)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_diff(directory: str) -> str | None:
    """Return ``git diff HEAD`` output, or None if not a git repo / no diff."""
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD", "--stat"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() or None
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
