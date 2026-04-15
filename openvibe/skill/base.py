"""Core skill abstractions.

A ``SkillDefinition`` is a named, discoverable prompt-template that routes
through the LLM (unlike slash commands which execute locally).  Skills carry
rich metadata used for:

* **Discovery** — capability/tag-based search and ranking.
* **Execution** — retry policy, fallback skill, validator chain.
* **Observability** — cost tier, reliability score updated from the log.

Bundled skills live in ``openvibe.skill.bundled``.
Custom skills can be registered via :func:`register_skill`.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CostTier(StrEnum):
    """Expected relative cost of running this skill one time."""

    LOW = "low"       # < 5 tool calls
    MEDIUM = "medium" # 5–20 tool calls
    HIGH = "high"     # 20+ tool calls or expensive tools (web, long bash)


class SkillStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"   # completed with caveats / validation warnings
    RETRIED = "retried"   # succeeded after ≥1 retry
    FALLBACK = "fallback" # succeeded via fallback_skill
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Data objects
# ---------------------------------------------------------------------------


@dataclass
class SkillExample:
    """A concrete example that helps users and the ranking algorithm understand the skill."""

    input: str         # e.g. "the tests are failing after refactor"
    description: str   # e.g. "diagnose a regression introduced by a refactor"


@dataclass
class SkillResult:
    """Output of a single skill execution attempt."""

    skill_name: str
    status: SkillStatus
    output: str
    attempt: int = 1
    elapsed: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Outcome of running a :class:`SkillValidator` against a :class:`SkillResult`."""

    passed: bool
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    can_retry: bool = True
    retry_hint: str = ""  # appended to the retry prompt to guide the next attempt


class SkillValidator(abc.ABC):
    """Abstract base for validators that inspect a :class:`SkillResult`."""

    name: str = "validator"

    @abc.abstractmethod
    def validate(
        self, result: "SkillResult", context: dict[str, Any]
    ) -> ValidationResult:
        """Return a :class:`ValidationResult`.  ``passed=True`` means OK."""
        ...


# ---------------------------------------------------------------------------
# SkillDefinition
# ---------------------------------------------------------------------------


class SkillDefinition(abc.ABC):
    """Abstract base for all skills.

    Subclass this and implement :meth:`get_prompt`.  Class-level attributes
    provide all metadata; no ``__init__`` override is needed for simple skills.

    Example::

        class MySkill(SkillDefinition):
            name = "myskill"
            description = "Does X to Y."
            tags = ["x", "y"]
            cost_estimate = CostTier.LOW

            def get_prompt(self, args: str) -> str:
                return f"Do X to {args or 'this code'}."
    """

    # --- Identity ---
    name: str = ""
    description: str = ""
    aliases: list[str] = []

    # --- Discovery metadata ---
    capabilities: list[str] = []   # e.g. ["code_review", "refactoring"]
    input_types: list[str] = []    # e.g. ["code", "file_path", "error_message"]
    output_types: list[str] = []   # e.g. ["code_diff", "report", "commit_message"]
    constraints: list[str] = []    # e.g. ["requires_git", "read_only"]
    tags: list[str] = []           # free-form search tokens
    cost_estimate: CostTier = CostTier.MEDIUM
    reliability: float = 1.0       # 0.0–1.0; updated in-place by the feedback loop
    examples: list[SkillExample] = []

    # --- UX ---
    user_invocable: bool = True    # show in /skills list
    when_to_use: str = ""          # one-line hint shown in /skills
    argument_hint: str = ""        # e.g. "[focus area]"

    # --- Execution control ---
    max_retries: int = 0           # extra attempts beyond the first (0 = no retry)
    fallback_skill: str | None = None  # skill name to try after all retries exhausted

    # --- Validators ---
    validators: list[SkillValidator] = []

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def get_prompt(self, args: str) -> str:
        """Return the LLM prompt for this invocation."""
        ...

    def get_retry_prompt(self, args: str, attempt: int, hint: str) -> str:
        """Return a modified prompt for retry attempt *attempt* (1-indexed).

        Default: same as :meth:`get_prompt` with an appended hint block.
        Override for skill-specific retry strategies.
        """
        base = self.get_prompt(args)
        if hint:
            return (
                f"{base}\n\n"
                f"**Previous attempt {attempt - 1} did not satisfy requirements.**\n"
                f"Hint for this attempt: {hint}"
            )
        return base
