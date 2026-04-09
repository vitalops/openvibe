"""openvibe skill system.

Skills are prompt templates routed through the LLM, unlike slash commands
which execute locally.  The user types ``/skillname [args]`` and the session
expands it into a structured prompt before sending it to the model.

Public API::

    from openvibe.skill import SkillDefinition, SkillRegistry, get_registry, register_skill
    from openvibe.skill.bundled import init_bundled_skills
"""

from openvibe.skill.base import (
    SkillDefinition,
    SkillRegistry,
    get_registry,
    register_skill,
)

__all__ = [
    "SkillDefinition",
    "SkillRegistry",
    "get_registry",
    "register_skill",
]
