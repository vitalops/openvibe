"""Bundled skills — registered once via :func:`init_bundled_skills`."""

from __future__ import annotations

from openvibe.skill.registry import get_registry


def init_bundled_skills() -> None:
    """Register all bundled skills in the global registry.

    Called from :meth:`~openvibe.api.OpenVibe.start` and
    :meth:`~openvibe.api.OpenVibe.start_async`.
    """
    from openvibe.skill.bundled.build_and_eval import BuildAndEvalSkill
    from openvibe.skill.bundled.simulate import SimulateSkill

    registry = get_registry()
    registry.register(SimulateSkill())
    registry.register(BuildAndEvalSkill())
