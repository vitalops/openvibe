"""Evaluator — LLM-as-judge scoring using openvibe's LLMBackend.

Same ``LLMBackend.stream()`` pattern as ``SessionProcessor`` and ``WorldSimulator``.
No raw litellm calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from openvibe.llm import LLMBackend, Message, StreamDone, TextDelta

from openvibe.sim.scenario import (
    CriterionScore,
    Dataset,
    EvaluationCriterion,
    EvaluationReport,
    EvaluationResult,
    Scenario,
    SimEnvironment,
    SimulationResult,
    Turn,
)

logger = logging.getLogger(__name__)

_JUDGE_SYSTEM = (
    "You are an expert evaluator assessing an AI agent's performance in an enterprise workflow simulation. "
    "Evaluate objectively based on the transcript and rubric. "
    "Always reply with valid JSON only — no markdown fences, no prose."
)

_JUDGE_PROMPT = """\
## Scenario
Title: {title}
Difficulty: {difficulty}
Expected outcome: {expected_outcome}
Actual outcome: {actual_outcome}
Steps taken: {steps_taken}

## Business Constraints (the agent MUST follow these)
{constraints}

## Evaluation Criteria
{criteria_text}

## Conversation Transcript
{transcript}

Score only the AGENT's responses (not the customer/persona).
For each criterion, assign a score 0.0-1.0 based on the rubric.

Return exactly this JSON:
{{
  "scores": {{
    "<criterion_name>": {{
      "score": 0.0-1.0,
      "rationale": "1-2 sentence evidence-based justification"
    }}
  }},
  "overall_feedback": "2-3 sentence summary of agent performance",
  "improvement_suggestions": ["suggestion1", "suggestion2"]
}}"""


class Evaluator:
    """Scores simulation results using openvibe's LLMBackend as judge."""

    def __init__(
        self,
        llm: LLMBackend,
        model: str,
        agent_name: str = "agent",
        max_concurrency: int = 5,
    ) -> None:
        self._llm = llm
        self._model = model
        self._agent_name = agent_name
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def evaluate(
        self,
        scenario: Scenario,
        sim_result: SimulationResult,
    ) -> EvaluationResult:
        """Evaluate one simulation result."""
        start = time.time()
        criteria = scenario.evaluation_criteria

        prompt = _JUDGE_PROMPT.format(
            title=scenario.title,
            difficulty=scenario.metadata.difficulty,
            expected_outcome=scenario.expected_outcome,
            actual_outcome=sim_result.actual_outcome,
            steps_taken=sim_result.steps_taken,
            constraints="\n".join(f"- {c}" for c in scenario.constraints),
            criteria_text=_format_criteria(criteria),
            transcript=_format_transcript(sim_result.full_conversation),
        )

        async with self._semaphore:
            try:
                raw = await self._complete(_JUDGE_SYSTEM, prompt)
                parsed = _parse_judge_json(raw)
            except Exception as exc:
                logger.error("Judge call failed: %s", exc)
                parsed = _fallback_scores(criteria)

        criterion_scores: list[CriterionScore] = []
        for crit in criteria:
            raw_score = parsed.get("scores", {}).get(crit.name, {})
            score = max(0.0, min(1.0, float(raw_score.get("score", 0.0))))
            criterion_scores.append(
                CriterionScore(
                    criterion=crit.name,
                    score=score,
                    weighted_score=score * crit.weight,
                    rationale=raw_score.get("rationale", ""),
                )
            )

        total_score = sum(cs.weighted_score for cs in criterion_scores)
        outcome_match = sim_result.actual_outcome == scenario.expected_outcome

        return EvaluationResult(
            scenario_id=scenario.metadata.scenario_id,
            scenario_title=scenario.title,
            difficulty=scenario.metadata.difficulty,
            agent_name=self._agent_name,
            model=self._model,
            criterion_scores=criterion_scores,
            total_score=total_score,
            outcome_match=outcome_match,
            feedback=parsed.get("overall_feedback", ""),
            improvement_suggestions=parsed.get("improvement_suggestions", []),
            elapsed_ms=int((time.time() - start) * 1000),
        )

    async def evaluate_batch(
        self,
        pairs: list[tuple[Scenario, SimulationResult]],
    ) -> list[EvaluationResult]:
        """Evaluate multiple pairs concurrently."""
        tasks = [self.evaluate(s, r) for s, r in pairs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: list[EvaluationResult] = []
        for r in results:
            if isinstance(r, BaseException):
                logger.error("Evaluation failed: %s", r)
            else:
                out.append(r)
        return out

    # ------------------------------------------------------------------
    # LLMBackend call — same pattern as SessionProcessor
    # ------------------------------------------------------------------

    async def _complete(self, system: str, user: str) -> str:
        messages = [Message(role="user", content=user)]
        chunks: list[str] = []
        async for event in self._llm.stream(
            model=self._model,
            messages=messages,
            system=system,
        ):
            if isinstance(event, TextDelta):
                chunks.append(event.content)
            elif isinstance(event, StreamDone):
                break
        return "".join(chunks)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_criteria(criteria: list[EvaluationCriterion]) -> str:
    lines: list[str] = []
    for c in criteria:
        lines.append(f"**{c.name}** (weight {c.weight:.0%}): {c.description}")
        for bucket, desc in c.rubric.items():
            lines.append(f"  {bucket}: {desc}")
    return "\n".join(lines)


def _format_transcript(turns: list[Turn]) -> str:
    return "\n".join(f"[{i+1}] {t.role.upper()}: {t.content}" for i, t in enumerate(turns))


def _parse_judge_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def _fallback_scores(criteria: list[EvaluationCriterion]) -> dict[str, Any]:
    return {
        "scores": {c.name: {"score": 0.0, "rationale": "Evaluation error — judge unavailable."} for c in criteria},
        "overall_feedback": "Evaluation could not be completed.",
        "improvement_suggestions": [],
    }
