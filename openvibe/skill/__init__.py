"""openvibe skills — discoverable prompt-templates that route through the LLM.

Public surface::

    from openvibe.skill import (
        SkillDefinition,
        SkillResult,
        SkillStatus,
        CostTier,
        SkillValidator,
        ValidationResult,
        SkillExample,
        get_registry,
        register_skill,
        SkillExecutor,
        ExecutionContext,
        get_skill_log,
        init_skill_log,
        load_skills_dir,
        SkillLoader,
    )
"""

from openvibe.skill.base import (
    CostTier,
    SkillDefinition,
    SkillExample,
    SkillResult,
    SkillStatus,
    SkillValidator,
    ValidationResult,
)
from openvibe.skill.executor import ExecutionContext, SkillExecutor
from openvibe.skill.loader import FileSkill, SkillLoader, load_skills_dir
from openvibe.skill.log import get_skill_log, init_skill_log
from openvibe.skill.registry import get_registry, register_skill
from openvibe.skill.verifier import (
    KeywordValidator,
    MinLengthValidator,
    NoErrorValidator,
    NonEmptyValidator,
    SkillVerifier,
)

__all__ = [
    # base
    "CostTier",
    "SkillDefinition",
    "SkillExample",
    "SkillResult",
    "SkillStatus",
    "SkillValidator",
    "ValidationResult",
    # registry
    "get_registry",
    "register_skill",
    # executor
    "ExecutionContext",
    "SkillExecutor",
    # log
    "get_skill_log",
    "init_skill_log",
    # loader
    "FileSkill",
    "SkillLoader",
    "load_skills_dir",
    # validators
    "KeywordValidator",
    "MinLengthValidator",
    "NoErrorValidator",
    "NonEmptyValidator",
    "SkillVerifier",
]
