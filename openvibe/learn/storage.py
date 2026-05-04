"""Storage for learned task procedures.

All procedures are saved as JSON files under:
    <project_dir>/.openvibe/learned/<task_name>.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def procedures_dir(project_dir: Path) -> Path:
    d = project_dir / ".openvibe" / "learned"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(task_name: str) -> str:
    """Convert a task name to a safe filename stem."""
    name = task_name.lower().strip()
    name = re.sub(r"[^\w\- ]", "", name)
    name = re.sub(r"\s+", "_", name)
    return name or "task"


def procedure_path(project_dir: Path, task_name: str) -> Path:
    return procedures_dir(project_dir) / f"{_safe_name(task_name)}.json"


def save_procedure(project_dir: Path, task_name: str, data: dict) -> Path:
    path = procedure_path(project_dir, task_name)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return path


def load_procedure(project_dir: Path, task_name: str) -> dict | None:
    """Load a procedure by exact or fuzzy name match."""
    # Exact match first
    path = procedure_path(project_dir, task_name)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    # Fuzzy: any file whose stem contains the query
    needle = _safe_name(task_name)
    for f in sorted(procedures_dir(project_dir).glob("*.json")):
        if needle in f.stem:
            try:
                return json.loads(f.read_text())
            except Exception:
                pass
    return None


def list_procedures(project_dir: Path) -> list[dict]:
    """Return list of {name, description} dicts, sorted by name."""
    results = []
    for f in sorted(procedures_dir(project_dir).glob("*.json")):
        try:
            data = json.loads(f.read_text())
            results.append(
                {
                    "name": f.stem,
                    "description": data.get("description", ""),
                }
            )
        except Exception:
            results.append({"name": f.stem, "description": ""})
    return results
