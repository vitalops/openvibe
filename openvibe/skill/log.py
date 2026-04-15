"""Skill execution logging — observability and feedback loop.

Every skill execution is appended to an in-memory ring buffer and optionally
to a JSONL file on disk.  :meth:`SkillLog.compute_reliability` reads the
recent window to produce the 0–1 score that is written back to
``SkillDefinition.reliability``, closing the feedback loop.

Usage::

    from openvibe.skill.log import get_skill_log

    log = get_skill_log()
    print(log.stats("simplify"))
    # {'total': 12, 'success_rate': 0.916, 'failure_rate': 0.083, ...}
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openvibe.skill.base import SkillStatus


# ---------------------------------------------------------------------------
# Log entry
# ---------------------------------------------------------------------------


@dataclass
class SkillLogEntry:
    skill_name: str
    args: str
    status: str          # SkillStatus value
    attempt: int         # which attempt succeeded / finally failed
    elapsed: float       # total wall-clock seconds for all attempts
    timestamp: float = field(default_factory=time.time)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# SkillLog
# ---------------------------------------------------------------------------


class SkillLog:
    """Thread-safe in-memory skill log with optional JSONL persistence.

    The ring buffer caps at :attr:`MAX_ENTRIES_IN_MEMORY` so long-running
    processes don't grow unbounded.
    """

    MAX_ENTRIES_IN_MEMORY: int = 500
    # How many recent entries to consider for reliability scoring.
    RELIABILITY_WINDOW: int = 100

    def __init__(self, log_path: Path | None = None) -> None:
        self._path = log_path
        self._entries: list[SkillLogEntry] = []

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record(self, entry: SkillLogEntry) -> None:
        """Append *entry* to the log."""
        self._entries.append(entry)
        if len(self._entries) > self.MAX_ENTRIES_IN_MEMORY:
            self._entries = self._entries[-self.MAX_ENTRIES_IN_MEMORY :]
        if self._path:
            self._append_to_file(entry)

    # ------------------------------------------------------------------
    # Read / aggregate
    # ------------------------------------------------------------------

    def entries(self, skill_name: str | None = None) -> list[SkillLogEntry]:
        """Return entries, optionally filtered to a single skill."""
        if skill_name is None:
            return list(self._entries)
        return [e for e in self._entries if e.skill_name == skill_name]

    def stats(self, skill_name: str | None = None) -> dict[str, Any]:
        """Return aggregate statistics over logged entries.

        Keys: ``total``, ``success_rate``, ``failure_rate``,
        ``partial_rate``, ``retry_rate``, ``avg_elapsed_s``.
        """
        entries = self.entries(skill_name)
        if not entries:
            return {}

        total = len(entries)
        successes = sum(
            1
            for e in entries
            if e.status in (SkillStatus.SUCCESS, SkillStatus.RETRIED, SkillStatus.FALLBACK)
        )
        partials = sum(1 for e in entries if e.status == SkillStatus.PARTIAL)
        failures = sum(1 for e in entries if e.status == SkillStatus.FAILED)
        retried = sum(1 for e in entries if e.attempt > 1)
        avg_elapsed = sum(e.elapsed for e in entries) / total

        return {
            "total": total,
            "success_rate": round(successes / total, 4),
            "partial_rate": round(partials / total, 4),
            "failure_rate": round(failures / total, 4),
            "retry_rate": round(retried / total, 4),
            "avg_elapsed_s": round(avg_elapsed, 3),
        }

    def compute_reliability(self, skill_name: str) -> float:
        """Return a 0–1 reliability score from the most recent executions.

        Counts SUCCESS, RETRIED, PARTIAL, and FALLBACK as "good" outcomes.
        Returns ``1.0`` when there is no history (benefit of the doubt).
        """
        window = [
            e
            for e in self._entries[-self.RELIABILITY_WINDOW :]
            if e.skill_name == skill_name
        ]
        if not window:
            return 1.0
        good_statuses = {
            SkillStatus.SUCCESS,
            SkillStatus.RETRIED,
            SkillStatus.PARTIAL,
            SkillStatus.FALLBACK,
        }
        good = sum(1 for e in window if e.status in good_statuses)
        return round(good / len(window), 4)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _append_to_file(self, entry: SkillLogEntry) -> None:
        try:
            with self._path.open("a", encoding="utf-8") as f:  # type: ignore[union-attr]
                f.write(json.dumps(asdict(entry)) + "\n")
        except OSError:
            pass  # log failures are non-fatal


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_global_log: SkillLog = SkillLog()


def get_skill_log() -> SkillLog:
    """Return the global :class:`SkillLog` singleton."""
    return _global_log


def init_skill_log(log_path: Path | None = None) -> SkillLog:
    """(Re-)initialise the global log, optionally attaching a JSONL file."""
    global _global_log
    _global_log = SkillLog(log_path=log_path)
    return _global_log
