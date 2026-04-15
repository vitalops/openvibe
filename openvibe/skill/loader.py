"""Load skills from a ``skills/`` directory tree.

Expected layout::

    skills/
        summarize/
            SKILL.md
        my-custom-skill/
            SKILL.md

Each ``SKILL.md`` file contains optional YAML front-matter followed by the
prompt template.  ``{{args}}`` in the template is replaced with the user's
arguments at invocation time::

    ---
    description: Summarize any content concisely.
    aliases: [sum, tldr]
    tags: [summary, overview]
    cost: low
    when_to_use: When you need a quick overview of something lengthy.
    argument_hint: "[topic or text]"
    ---

    Summarize: {{args}}

    Capture the key points in bullet form.  End with a one-sentence takeaway.

Front-matter keys (all optional)
---------------------------------
* ``description`` — shown in ``/skills``
* ``aliases``     — list of shorthand names
* ``tags``        — list of search tokens
* ``capabilities``  — list of capability labels
* ``input_types`` / ``output_types`` — list of type labels
* ``cost``        — ``low`` | ``medium`` | ``high``  (default ``medium``)
* ``when_to_use`` — one-line hint shown in ``/skills``
* ``argument_hint`` — e.g. ``[topic]``
* ``max_retries`` — int (default ``0``)
* ``fallback``    — name of another skill to try on failure
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openvibe.skill.base import CostTier, SkillDefinition
from openvibe.skill.registry import SkillRegistry, get_registry

_FRONTMATTER_RE = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n", re.DOTALL)


# ---------------------------------------------------------------------------
# FileSkill — a SkillDefinition backed by a SKILL.md file
# ---------------------------------------------------------------------------


class FileSkill(SkillDefinition):
    """A skill loaded from a ``SKILL.md`` file."""

    def __init__(
        self,
        name: str,
        prompt_template: str,
        meta: dict[str, Any],
    ) -> None:
        self.name = name
        self.description = str(meta.get("description", ""))
        self.aliases = list(meta.get("aliases") or [])
        self.tags = list(meta.get("tags") or [])
        self.capabilities = list(meta.get("capabilities") or [])
        self.input_types = list(meta.get("input_types") or [])
        self.output_types = list(meta.get("output_types") or [])
        self.when_to_use = str(meta.get("when_to_use", ""))
        self.argument_hint = str(meta.get("argument_hint", ""))
        self.max_retries = int(meta.get("max_retries", 0))
        self.fallback_skill = meta.get("fallback") or None
        self.user_invocable = True
        self._template = prompt_template.strip()

        # cost
        cost_raw = str(meta.get("cost", "medium")).lower()
        self.cost_estimate = CostTier(cost_raw) if cost_raw in CostTier._value2member_map_ else CostTier.MEDIUM  # type: ignore[attr-defined]

    def get_prompt(self, args: str) -> str:
        return self._template.replace("{{args}}", args).strip()


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class SkillLoader:
    """Discovers and loads skills from a directory tree.

    Only the ``skill-name/SKILL.md`` layout is supported.  Directories
    without a ``SKILL.md`` are silently skipped.
    """

    SKILL_FILE = "SKILL.md"

    def __init__(self, registry: SkillRegistry | None = None) -> None:
        self._registry = registry or get_registry()

    def load(self, skills_dir: Path | str) -> list[FileSkill]:
        """Load all skills found under *skills_dir* and register them.

        Returns the list of successfully loaded :class:`FileSkill` instances.
        Errors in individual files are logged but do not abort the load.
        """
        skills_dir = Path(skills_dir)
        if not skills_dir.is_dir():
            return []

        loaded: list[FileSkill] = []
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_file = entry / self.SKILL_FILE
            if not skill_file.is_file():
                continue
            skill = self._load_file(entry.name, skill_file)
            if skill is not None:
                self._registry.register(skill)
                loaded.append(skill)

        return loaded

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_file(self, dir_name: str, skill_file: Path) -> "FileSkill | None":
        try:
            raw = skill_file.read_text(encoding="utf-8")
        except OSError as exc:
            import logging
            logging.getLogger(__name__).warning("Could not read %s: %s", skill_file, exc)
            return None

        meta, template = _parse_skill_md(raw)

        # Derive skill name: front-matter ``name`` overrides directory name
        name = str(meta.pop("name", dir_name)).lower().replace(" ", "-")

        if not template.strip():
            import logging
            logging.getLogger(__name__).warning(
                "Skipping %s: no prompt template found (body is empty)", skill_file
            )
            return None

        return FileSkill(name=name, prompt_template=template, meta=meta)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_skill_md(text: str) -> tuple[dict[str, Any], str]:
    """Split a SKILL.md into ``(front_matter_dict, prompt_template)``."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    meta = _parse_yaml_lite(m.group(1))
    template = text[m.end():]
    return meta, template


def _parse_yaml_lite(text: str) -> dict[str, Any]:
    """Minimal YAML parser that handles the subset used in SKILL.md front-matter.

    Supports:
    * ``key: scalar value``
    * ``key: [item1, item2]``   (inline list)
    * Block lists::

        key:
          - item1
          - item2

    Falls back to the ``yaml`` package when available for full YAML support.
    """
    try:
        import yaml  # type: ignore[import]
        return yaml.safe_load(text) or {}
    except ImportError:
        pass

    result: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue

        m = re.match(r"^(\w[\w_-]*):\s*(.*)", line)
        if not m:
            i += 1
            continue

        key = m.group(1)
        value_str = m.group(2).strip()

        if value_str.startswith("[") and value_str.endswith("]"):
            # Inline list: [a, b, c]
            inner = value_str[1:-1]
            result[key] = [v.strip().strip("\"'") for v in inner.split(",") if v.strip()]
        elif not value_str:
            # Potential block list
            items: list[str] = []
            i += 1
            while i < len(lines) and lines[i].lstrip().startswith("-"):
                items.append(lines[i].lstrip().lstrip("-").strip().strip("\"'"))
                i += 1
            result[key] = items
            continue
        else:
            # Scalar — strip surrounding quotes
            result[key] = value_str.strip("\"'")

        i += 1

    return result


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def load_skills_dir(
    skills_dir: Path | str,
    registry: SkillRegistry | None = None,
) -> list[FileSkill]:
    """Load skills from *skills_dir* into *registry* (defaults to global registry).

    This is the main entry point for external callers::

        from openvibe.skill.loader import load_skills_dir
        load_skills_dir(Path("./skills"))
    """
    return SkillLoader(registry).load(skills_dir)
