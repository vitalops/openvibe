"""SkillRegistry — registration, indexing, search, and reliability-weighted ranking.

Skills are indexed by name and alias.  :meth:`SkillRegistry.search` supports
keyword queries over name, description, tags, capabilities, and
input/output types, with a reliability multiplier that gradually demotes
consistently-failing skills.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openvibe.skill.base import SkillDefinition


class SkillRegistry:
    """Global registry of all :class:`~openvibe.skill.base.SkillDefinition` instances."""

    def __init__(self) -> None:
        self._by_name: dict[str, "SkillDefinition"] = {}
        self._by_alias: dict[str, str] = {}  # alias → canonical name

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, skill: "SkillDefinition") -> None:
        """Register *skill*.  Overwrites any existing skill with the same name."""
        self._by_name[skill.name] = skill
        for alias in skill.aliases:
            self._by_alias[alias.lower()] = skill.name

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get(self, name: str) -> "SkillDefinition | None":
        """Look up a skill by name or alias (case-insensitive)."""
        name_lower = name.lower()
        skill = self._by_name.get(name_lower) or self._by_name.get(name)
        if skill:
            return skill
        canonical = self._by_alias.get(name_lower)
        if canonical:
            return self._by_name.get(canonical)
        return None

    def all(self) -> list["SkillDefinition"]:
        """Return all registered skills in registration order."""
        return list(self._by_name.values())

    def user_invocable(self) -> list["SkillDefinition"]:
        """Return skills that should appear in ``/skills`` output."""
        return [s for s in self._by_name.values() if s.user_invocable]

    # ------------------------------------------------------------------
    # Search & ranking
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 5) -> list[tuple["SkillDefinition", float]]:
        """Return up to *top_k* skills ranked by relevance to *query*.

        Scoring:
        * Exact name / alias match → +10 / +8
        * Keyword overlap with description → +1 per word
        * Tag / capability / type match → +2 per token
        * Reliability multiplier: ``0.5 + 0.5 * skill.reliability``
          (a skill with reliability=0 is scored at 50 % of its raw score)
        """
        query_tokens = _tokenise(query)
        results: list[tuple["SkillDefinition", float]] = []

        for skill in self._by_name.values():
            score = self._score(skill, query_tokens)
            if score > 0:
                results.append((skill, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def find_best(self, query: str) -> "SkillDefinition | None":
        """Return the single highest-ranked skill for *query*, or ``None``."""
        results = self.search(query, top_k=1)
        return results[0][0] if results else None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _score(self, skill: "SkillDefinition", query_tokens: set[str]) -> float:
        score = 0.0

        # Exact name / alias hit
        if skill.name.lower() in query_tokens:
            score += 10.0
        for alias in skill.aliases:
            if alias.lower() in query_tokens:
                score += 8.0

        # Description overlap
        desc_tokens = _tokenise(skill.description)
        score += len(query_tokens & desc_tokens) * 1.0

        # Tags + capabilities + input/output types
        meta_tokens = _tokenise(
            " ".join(skill.tags + skill.capabilities + skill.input_types + skill.output_types)
        )
        score += len(query_tokens & meta_tokens) * 2.0

        # when_to_use overlap
        use_tokens = _tokenise(skill.when_to_use)
        score += len(query_tokens & use_tokens) * 1.5

        # Reliability weight: unreliable skills are demoted but not excluded
        score *= 0.5 + 0.5 * max(0.0, min(1.0, skill.reliability))

        return score


# ---------------------------------------------------------------------------
# Module-level singleton + helpers
# ---------------------------------------------------------------------------


_registry: SkillRegistry = SkillRegistry()


def get_registry() -> SkillRegistry:
    """Return the global :class:`SkillRegistry` singleton."""
    return _registry


def register_skill(skill: "SkillDefinition") -> None:
    """Register *skill* in the global registry."""
    _registry.register(skill)


def _tokenise(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))
