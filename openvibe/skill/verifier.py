"""Skill execution verification — built-in validators and the SkillVerifier.

Built-in validators
-------------------
* :class:`NonEmptyValidator`  — output must be non-empty.
* :class:`NoErrorValidator`   — result must not carry an error flag.
* :class:`KeywordValidator`   — output must contain required / not forbidden words.
* :class:`MinLengthValidator` — output must exceed a character threshold.

Custom validators
-----------------
Subclass :class:`~openvibe.skill.base.SkillValidator` and add instances to
``SkillDefinition.validators``.  The verifier short-circuits on the first
failure, returning its :class:`~openvibe.skill.base.ValidationResult`.
"""

from __future__ import annotations

from typing import Any

from openvibe.skill.base import SkillResult, SkillValidator, ValidationResult


# ---------------------------------------------------------------------------
# Built-in validators
# ---------------------------------------------------------------------------


class NonEmptyValidator(SkillValidator):
    """Fails when the skill output is empty or whitespace-only."""

    name = "non_empty"

    def validate(self, result: SkillResult, context: dict[str, Any]) -> ValidationResult:
        if not result.output.strip():
            return ValidationResult(
                passed=False,
                reason="Skill produced no output.",
                can_retry=True,
                retry_hint="Produce a complete, non-empty response.",
            )
        return ValidationResult(passed=True)


class NoErrorValidator(SkillValidator):
    """Fails when the result carries an error."""

    name = "no_error"

    def validate(self, result: SkillResult, context: dict[str, Any]) -> ValidationResult:
        if result.error:
            return ValidationResult(
                passed=False,
                reason=f"Skill returned an error: {result.error}",
                can_retry=True,
                retry_hint="The previous attempt raised an error. Try a different approach.",
            )
        return ValidationResult(passed=True)


class KeywordValidator(SkillValidator):
    """Ensures the output contains required phrases and lacks forbidden ones."""

    name = "keyword"

    def __init__(
        self,
        required: list[str] | None = None,
        forbidden: list[str] | None = None,
    ) -> None:
        self._required = [k.lower() for k in (required or [])]
        self._forbidden = [k.lower() for k in (forbidden or [])]

    def validate(self, result: SkillResult, context: dict[str, Any]) -> ValidationResult:
        lower = result.output.lower()
        for kw in self._required:
            if kw not in lower:
                return ValidationResult(
                    passed=False,
                    reason=f"Required phrase '{kw}' not found in output.",
                    can_retry=True,
                    retry_hint=f"Make sure the response includes '{kw}'.",
                )
        for kw in self._forbidden:
            if kw in lower:
                return ValidationResult(
                    passed=False,
                    reason=f"Forbidden phrase '{kw}' found in output.",
                    can_retry=True,
                    retry_hint=f"Do not include '{kw}' in the response.",
                )
        return ValidationResult(passed=True)


class MinLengthValidator(SkillValidator):
    """Fails when the output is shorter than *min_chars* characters."""

    name = "min_length"

    def __init__(self, min_chars: int = 50) -> None:
        self._min = min_chars

    def validate(self, result: SkillResult, context: dict[str, Any]) -> ValidationResult:
        length = len(result.output.strip())
        if length < self._min:
            return ValidationResult(
                passed=False,
                reason=f"Output too short ({length} chars, minimum {self._min}).",
                can_retry=True,
                retry_hint=f"Provide a more complete response (at least {self._min} characters).",
            )
        return ValidationResult(passed=True)


# ---------------------------------------------------------------------------
# SkillVerifier
# ---------------------------------------------------------------------------


class SkillVerifier:
    """Runs a skill's validator chain and returns the first failure, or a pass."""

    def verify(
        self,
        validators: list[SkillValidator],
        result: SkillResult,
        context: dict[str, Any] | None = None,
    ) -> ValidationResult:
        """Run all *validators* in order; short-circuit on first failure.

        Returns :class:`~openvibe.skill.base.ValidationResult` with
        ``passed=True`` when all validators succeed (or the list is empty).
        """
        ctx = context or {}
        for validator in validators:
            vr = validator.validate(result, ctx)
            if not vr.passed:
                return vr
        return ValidationResult(passed=True, reason="All validators passed.")
