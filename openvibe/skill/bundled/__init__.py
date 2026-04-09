"""Bundled (built-in) skills for openvibe.

Call ``init_bundled_skills()`` once at startup to register all bundled skills
in the global registry.  This is called automatically by ``OpenVibe.start()``
and ``OpenVibe.start_async()``.
"""

from __future__ import annotations

from openvibe.skill.base import get_registry
from openvibe.skill.bundled.commit import CommitSkill
from openvibe.skill.bundled.debug import DebugSkill
from openvibe.skill.bundled.plan import PlanSkill
from openvibe.skill.bundled.simplify import SimplifySkill

__all__ = ["init_bundled_skills"]

_BUNDLED: list[type] = [
    SimplifySkill,
    DebugSkill,
    PlanSkill,
    CommitSkill,
]


def init_bundled_skills() -> None:
    """Register all bundled skills in the global registry.

    Safe to call multiple times — skills are registered by name so duplicate
    calls simply overwrite with the same instance.
    """
    registry = get_registry()
    for cls in _BUNDLED:
        registry.register(cls())
