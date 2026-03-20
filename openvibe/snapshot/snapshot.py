"""File snapshot and diff utilities.

After each assistant turn, openvibe records which files changed so the
session summary can show a meaningful diff and to enable revert-to-snapshot.

Snapshots are lightweight: we only store file hashes and modification times,
not full file contents.  A full snapshot (for revert) copies changed files
into ``~/.openvibe/snapshots/<session_id>/<timestamp>/``.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileEntry:
    path: str           # project-relative path
    hash: str           # SHA-256 hex digest
    size: int
    is_new: bool = False
    is_deleted: bool = False


@dataclass
class Snapshot:
    session_id: str
    timestamp: str
    base_dir: str
    files: dict[str, FileEntry] = field(default_factory=dict)


@dataclass
class DiffSummary:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        return len(self.added) + len(self.modified) + len(self.deleted)

    def is_empty(self) -> bool:
        return self.total_changes == 0


# ---------------------------------------------------------------------------
# Snapshot creation
# ---------------------------------------------------------------------------

def take_snapshot(base_dir: str, extensions: list[str] | None = None) -> Snapshot:
    """Walk *base_dir* and record a hash of every text file."""
    from openvibe.session.models import now_iso

    base = Path(base_dir)
    entries: dict[str, FileEntry] = {}

    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if _should_skip(path):
            continue
        if extensions and path.suffix not in extensions:
            continue

        rel = str(path.relative_to(base))
        try:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            entries[rel] = FileEntry(path=rel, hash=digest, size=len(data))
        except OSError:
            continue

    return Snapshot(
        session_id="",
        timestamp=now_iso(),
        base_dir=base_dir,
        files=entries,
    )


def diff_snapshots(before: Snapshot, after: Snapshot) -> DiffSummary:
    """Compare two snapshots and return a summary of changes."""
    summary = DiffSummary()

    before_paths = set(before.files)
    after_paths = set(after.files)

    summary.added = sorted(after_paths - before_paths)
    summary.deleted = sorted(before_paths - after_paths)
    summary.modified = sorted(
        p for p in before_paths & after_paths
        if before.files[p].hash != after.files[p].hash
    )

    return summary


# ---------------------------------------------------------------------------
# Revert support
# ---------------------------------------------------------------------------

def save_revert_snapshot(
    session_id: str,
    base_dir: str,
    snapshot: Snapshot,
    data_dir: Path | None = None,
) -> Path:
    """Copy changed files into a revert archive directory.

    Returns the archive path so it can be stored on the session record.
    """
    if data_dir is None:
        data_dir = Path.home() / ".openvibe"

    archive_dir = data_dir / "snapshots" / session_id / snapshot.timestamp.replace(":", "-")
    archive_dir.mkdir(parents=True, exist_ok=True)

    base = Path(base_dir)
    for rel, entry in snapshot.files.items():
        src = base / rel
        dst = archive_dir / rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    return archive_dir


def revert_to_snapshot(archive_dir: Path, base_dir: str) -> list[str]:
    """Restore files from an archive directory back into *base_dir*.

    Returns a list of restored file paths (project-relative).
    """
    base = Path(base_dir)
    restored: list[str] = []

    for src in archive_dir.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(archive_dir)
        dst = base / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        restored.append(str(rel))

    return restored


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SKIP_DIRS = {".git", ".hg", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"}


def _should_skip(path: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts)
