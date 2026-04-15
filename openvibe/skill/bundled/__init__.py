"""Bundled skills — registered once via :func:`init_bundled_skills`."""

from __future__ import annotations

from openvibe.skill.registry import get_registry


def init_bundled_skills() -> None:
    """Register all bundled skills in the global registry.

    Called from :meth:`~openvibe.api.OpenVibe.start` and
    :meth:`~openvibe.api.OpenVibe.start_async`.
    """
    from openvibe.skill.bundled.brainstorm import BrainstormSkill
    from openvibe.skill.bundled.draft import DraftSkill
    from openvibe.skill.bundled.explain import ExplainSkill
    from openvibe.skill.bundled.summarize import SummarizeSkill

    registry = get_registry()
    registry.register(SummarizeSkill())
    registry.register(ExplainSkill())
    registry.register(BrainstormSkill())
    registry.register(DraftSkill())
