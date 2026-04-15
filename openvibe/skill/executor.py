"""SkillExecutor — agent-skill loop with retry, fallback, and observability.

The executor drives one complete skill invocation:

1. **Select** — caller already resolved the skill via :class:`~openvibe.skill.registry.SkillRegistry`.
2. **Expand** — ``skill.get_prompt(args)`` builds the LLM prompt.
3. **Execute** — ``send_fn(prompt)`` runs the full agent loop and returns text.
4. **Verify** — :class:`~openvibe.skill.verifier.SkillVerifier` checks validators.
5. **Retry** — if verification failed and retries remain, rebuild prompt with hint.
6. **Fallback** — if all retries exhausted and a fallback skill is named, delegate.
7. **Log** — record outcome in :func:`~openvibe.skill.log.get_skill_log`.
8. **Update reliability** — write back ``skill.reliability`` from log.

The ``send_fn`` callable is intentionally simple: ``(prompt: str) -> str``.
In :class:`~openvibe.api.Session` this is wired to a thin wrapper around the
internal worker so that skills can trigger the full agent + tool loop.

Example (programmatic use)::

    from openvibe.skill.executor import SkillExecutor, ExecutionContext
    from openvibe.skill.registry import get_registry

    registry = get_registry()
    executor = SkillExecutor(registry)

    ctx = ExecutionContext(
        session_id=session.id,
        working_dir="/path/to/project",
        send_fn=lambda prompt: session._send_raw(prompt),
    )

    result = executor.run(registry.get("debug"), "TypeError in auth.py", ctx)
    print(result.status, result.output)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from openvibe.skill.base import SkillResult, SkillStatus, ValidationResult
from openvibe.skill.log import SkillLogEntry, get_skill_log
from openvibe.skill.verifier import SkillVerifier

if TYPE_CHECKING:
    from openvibe.skill.base import SkillDefinition
    from openvibe.skill.registry import SkillRegistry


# ---------------------------------------------------------------------------
# ExecutionContext
# ---------------------------------------------------------------------------


@dataclass
class ExecutionContext:
    """Everything the executor needs to send prompts and identify the caller.

    ``send_fn`` is the only required callable.  It must accept a prompt string
    and return the assistant's complete text response.  It is allowed to block;
    the executor is synchronous.
    """

    session_id: str
    working_dir: str
    send_fn: Callable[[str], str]


# ---------------------------------------------------------------------------
# SkillExecutor
# ---------------------------------------------------------------------------


class SkillExecutor:
    """Runs one skill invocation through the full agent-skill loop."""

    def __init__(self, registry: "SkillRegistry") -> None:
        self._registry = registry
        self._verifier = SkillVerifier()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        skill: "SkillDefinition",
        args: str,
        ctx: ExecutionContext,
    ) -> SkillResult:
        """Execute *skill* synchronously and return a :class:`SkillResult`.

        Retry and fallback logic is transparent to the caller.
        """
        start = time.monotonic()
        last_result: SkillResult | None = None
        last_validation: ValidationResult | None = None

        max_attempts = max(1, 1 + skill.max_retries)

        for attempt in range(1, max_attempts + 1):
            hint = (
                last_validation.retry_hint
                if (last_validation and not last_validation.passed)
                else ""
            )
            prompt = (
                skill.get_prompt(args)
                if attempt == 1
                else skill.get_retry_prompt(args, attempt, hint)
            )

            result = self._call_llm(skill, prompt, attempt, start, ctx)
            validation = self._verifier.verify(skill.validators, result)

            if validation.passed:
                if attempt > 1:
                    result.status = SkillStatus.RETRIED
                self._finish(skill, args, result)
                return result

            last_result = result
            last_validation = validation

            if not validation.can_retry or attempt >= max_attempts:
                break

        # All attempts exhausted → try fallback
        if skill.fallback_skill:
            fallback = self._registry.get(skill.fallback_skill)
            if fallback and fallback.name != skill.name:
                fb_result = self.run(fallback, args, ctx)
                fb_result.status = SkillStatus.FALLBACK
                fb_result.metadata["original_skill"] = skill.name
                fb_result.elapsed = time.monotonic() - start
                self._log(skill.name, args, fb_result)
                self._update_reliability(skill, success=False)
                return fb_result

        # Hard failure
        final = last_result or SkillResult(
            skill_name=skill.name,
            status=SkillStatus.FAILED,
            output="",
            error="No result produced.",
            elapsed=time.monotonic() - start,
        )
        final.status = SkillStatus.FAILED
        self._finish(skill, args, final, success=False)
        return final

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_llm(
        self,
        skill: "SkillDefinition",
        prompt: str,
        attempt: int,
        start: float,
        ctx: ExecutionContext,
    ) -> SkillResult:
        try:
            output = ctx.send_fn(prompt)
            return SkillResult(
                skill_name=skill.name,
                status=SkillStatus.SUCCESS,
                output=output,
                attempt=attempt,
                elapsed=time.monotonic() - start,
            )
        except Exception as exc:
            return SkillResult(
                skill_name=skill.name,
                status=SkillStatus.FAILED,
                output="",
                attempt=attempt,
                elapsed=time.monotonic() - start,
                error=str(exc),
            )

    def _finish(
        self,
        skill: "SkillDefinition",
        args: str,
        result: SkillResult,
        success: bool = True,
    ) -> None:
        self._log(skill.name, args, result)
        self._update_reliability(skill, success=success)

    def _log(self, skill_name: str, args: str, result: SkillResult) -> None:
        get_skill_log().record(
            SkillLogEntry(
                skill_name=skill_name,
                args=args,
                status=result.status,
                attempt=result.attempt,
                elapsed=result.elapsed,
                error=result.error,
                metadata=result.metadata,
            )
        )

    def _update_reliability(
        self, skill: "SkillDefinition", success: bool  # noqa: ARG002 (used implicitly via log)
    ) -> None:
        """Recompute and persist reliability on the live SkillDefinition object."""
        skill.reliability = get_skill_log().compute_reliability(skill.name)
