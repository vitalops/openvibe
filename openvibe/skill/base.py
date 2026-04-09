"""Skill base class and registry.

Skills are prompt templates that route through the LLM — unlike slash
commands, which execute locally and never touch the model.

When the user types ``/simplify`` the session detects it as a skill,
expands the skill's prompt via ``get_prompt()``, and passes that text to
the LLM as the user message.  From the LLM's perspective it is a normal
user message, so all tools and permissions remain in effect.

Implementing a custom skill
---------------------------
Subclass ``SkillDefinition`` and implement ``get_prompt()``::

    from openvibe.skill.base import SkillDefinition, get_registry

    class MySkill(SkillDefinition):
        name = "myskill"
        description = "Does something useful."
        aliases = ["ms"]

        def get_prompt(self, args: str) -> str:
            return f"Please do something useful with: {args}" if args else (
                "Please do something useful with the current codebase."
            )

    get_registry().register(MySkill())

Then call ``get_registry().register(MySkill())`` at import time or in your
startup hook.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# SkillDefinition
# ---------------------------------------------------------------------------


class SkillDefinition(abc.ABC):
    """Abstract base for all openvibe skills.

    Subclasses must set ``name`` and ``description`` as class attributes and
    implement ``get_prompt()``.
    """

    name: str  # must be set by subclass
    description: str  # must be set by subclass
    aliases: list[str] = []  # optional shorthand names
    user_invocable: bool = True  # show in /skills list
    when_to_use: str = ""  # hint shown in /skills
    argument_hint: str = ""  # e.g. "[focus area]" — shown after name in /help

    @abc.abstractmethod
    def get_prompt(self, args: str) -> str:
        """Return the expanded prompt to send to the LLM.

        *args* is everything the user typed after the skill name (may be empty).
        """
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SkillRegistry:
    """Holds all registered skills, keyed by name and aliases."""

    def __init__(self) -> None:
        # Primary lookup: name → skill
        self._skills: dict[str, SkillDefinition] = {}
        # Alias lookup: alias → primary name
        self._aliases: dict[str, str] = {}

    def register(self, skill: SkillDefinition) -> None:
        """Register *skill* by its name and any aliases."""
        self._skills[skill.name] = skill
        for alias in skill.aliases:
            self._aliases[alias] = skill.name

    def get(self, name: str) -> SkillDefinition | None:
        """Look up a skill by name or alias; returns None if not found."""
        if name in self._skills:
            return self._skills[name]
        primary = self._aliases.get(name)
        if primary:
            return self._skills.get(primary)
        return None

    def all(self) -> list[SkillDefinition]:
        """Return all registered skills (no duplicates from aliases)."""
        return list(self._skills.values())

    def user_invocable(self) -> list[SkillDefinition]:
        """Return all skills that should appear in /skills."""
        return [s for s in self._skills.values() if s.user_invocable]

    def __contains__(self, name: str) -> bool:
        return name in self._skills or name in self._aliases


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_registry: SkillRegistry | None = None


def get_registry() -> SkillRegistry:
    """Return the process-wide skill registry (created on first call)."""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry


def register_skill(skill: SkillDefinition) -> None:
    """Convenience wrapper: register *skill* in the global registry."""
    get_registry().register(skill)
