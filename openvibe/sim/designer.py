"""SimDesigner — LLM-powered environment and scenario designer.

Uses openvibe's ``LLMBackend`` directly (same as ``SessionProcessor``).
No raw litellm calls, no hardcoded domains.

The designer does two things:
1. ``design(context)``          → :class:`SimEnvironment`
   Given any plain-text description, the LLM designs the full environment:
   personas, tools, evaluation criteria, constraints, workflow rules,
   and a system prompt for the agent-under-test.

2. ``generate_scenarios(env, n)`` → list[:class:`Scenario`]
   Given the environment, generates *n* diverse test scenarios
   distributed across difficulty levels.

The context can be anything:
  - "Customer support for a SaaS billing system"
  - "Simulate an HR onboarding workflow at a 500-person tech company"
  - ""  (empty → LLM infers a realistic enterprise workflow)
  - Copied from the user's current openvibe session

Both calls share a private ``_complete()`` helper that collects text from
the ``LLMBackend.stream()`` async generator — the same call pattern used
by ``SessionProcessor``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from openvibe.llm import LLMBackend, Message, StreamDone, TextDelta

from openvibe.sim.scenario import (
    Dataset,
    DatasetMetadata,
    EvaluationCriterion,
    OutcomeStatus,
    PersonaTemplate,
    Scenario,
    ScenarioMetadata,
    SimEnvironment,
    ToolSpec,
    Turn,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON prompts — kept minimal and schema-focused so any LLM can follow them
# ---------------------------------------------------------------------------

_DESIGN_SYSTEM = (
    "You are an expert simulation environment designer. "
    "You design environments that test AI agent outputs by simulating realistic usage. "
    "Always reply with valid JSON only — no markdown fences, no prose, no explanations."
)

_DESIGN_PROMPT = """\
You are given context describing a task and what was produced to fulfil it.
Your job is to design a simulation environment that tests the quality of what was produced
by simulating how real users or systems would interact with it.

CONTEXT (task description + what was produced):
{context}

First, analyse the context:
- What did the task ask for?
- What was actually produced and how does it work?
- Who would realistically use or interact with it?
- What could go wrong or be done poorly?

Then design an environment whose scenarios will expose whether what was produced
works correctly, handles edge cases, and meets the original task requirements.

Return a single JSON object with exactly these fields:
{{
  "name": "Short environment name",
  "description": "1-2 sentence description of what is being tested",
  "personas": [
    {{
      "role": "role_name",
      "background": "Who this person is and why they are using what was produced",
      "personality_traits": ["trait1", "trait2"],
      "goals": ["goal1", "goal2"],
      "default_frustration": 0.0-1.0
    }}
  ],
  "tools": [
    {{
      "name": "snake_case_tool_name",
      "description": "What the tool does — mirror the actual capabilities of what was produced",
      "parameters": {{
        "type": "object",
        "properties": {{
          "param_name": {{"type": "string", "description": "..."}}
        }},
        "required": ["param_name"]
      }}
    }}
  ],
  "evaluation_criteria": [
    {{
      "name": "criterion_name",
      "description": "What aspect of the output is being measured",
      "weight": 0.0-1.0,
      "rubric": {{
        "0.0-0.4": "Poor: ...",
        "0.4-0.7": "Acceptable: ...",
        "0.7-1.0": "Excellent: ..."
      }}
    }}
  ],
  "constraints": ["Constraint derived from task requirements", "..."],
  "workflow_rules": ["Rule 1", "Rule 2"],
  "agent_system_prompt": "Full system prompt briefing the agent on what was produced and how to use it",
  "domain_hint": "Short free-text label for what is being tested"
}}

Requirements:
- Personas must be realistic users or stakeholders of what was produced
- Tools must reflect the actual capabilities of what was produced
- Evaluation criteria must directly test whether the task requirements were met
- Criteria weights must sum to exactly 1.0
- Constraints must be derived from the original task requirements
- The agent_system_prompt must give the agent full context of what was produced and how to operate it
- Everything must be specific to the context — do not invent unrelated domains
"""

_GENERATE_SYSTEM = (
    "You are a workflow scenario designer. "
    "You create realistic test scenarios for AI agent evaluation. "
    "Always reply with valid JSON only — no markdown fences, no prose."
)

_GENERATE_PROMPT = """\
Generate {n} diverse test scenarios for this simulation environment.

ENVIRONMENT: {env_name}
DESCRIPTION: {env_description}
AGENT ROLE: The agent handles interactions from the primary non-agent persona.
PRIMARY PERSONA ROLE: {primary_role}
TOOLS AVAILABLE: {tool_names}
CONSTRAINTS: {constraints}

Distribute scenarios across these difficulty levels:
- easy (~25%): straightforward, compliant requests
- medium (~35%): moderate complexity, some ambiguity
- hard (~30%): complex, multiple issues, policy edge cases
- adversarial (~10%): attempts to circumvent policy, false claims, unusual edge cases

Return a JSON array of scenario objects:
[
  {{
    "title": "Short scenario title",
    "description": "2-3 sentence situation description",
    "difficulty": "easy|medium|hard|adversarial",
    "opening_message": "The primary persona's opening message verbatim",
    "persona_details": {{
      "name": "First name",
      "background": "Specific context for this scenario",
      "frustration_level": 0.0-1.0,
      "specific_issue": "What exactly is their problem/request"
    }},
    "expected_outcome": "resolved|escalated|abandoned",
    "initial_context": {{"key": "value"}},
    "tags": ["tag1", "tag2"],
    "constraints_in_play": ["which constraints are relevant"]
  }}
]

Make scenarios realistic, diverse, and specific to the workflow. Return ONLY the JSON array.
"""

_MAX_RETRIES = 3
_RETRY_DELAY = 1.5


class SimDesigner:
    """Designs simulation environments and scenarios using openvibe's LLMBackend.

    Usage::

        from openvibe.llm import LiteLLMBackend
        llm = LiteLLMBackend()
        designer = SimDesigner(llm, model="anthropic/claude-sonnet-4-6")

        env = await designer.design("B2B SaaS customer support with billing issues")
        scenarios = await designer.generate_scenarios(env, n=20)
    """

    def __init__(self, llm: LLMBackend, model: str) -> None:
        self._llm = llm
        self._model = model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def design(self, context: str) -> SimEnvironment:
        """Design a complete SimEnvironment from any context string."""
        if not context.strip():
            context = (
                "A generic workflow where an AI agent handles requests "
                "and uses available tools to complete tasks."
            )

        prompt = _DESIGN_PROMPT.format(context=context.strip())
        raw = await self._complete_with_retry(
            system=_DESIGN_SYSTEM,
            user=prompt,
            label="environment design",
        )
        data = _parse_json(raw)
        return self._build_environment(data, context)

    async def generate_scenarios(
        self,
        env: SimEnvironment,
        n: int,
    ) -> list[Scenario]:
        """Generate *n* diverse scenarios for *env*."""
        primary_role = _primary_persona_role(env)
        tool_names = ", ".join(t.name for t in env.tools)
        constraints_text = "; ".join(env.constraints[:6])

        prompt = _GENERATE_PROMPT.format(
            n=n,
            env_name=env.name,
            env_description=env.description,
            primary_role=primary_role,
            tool_names=tool_names,
            constraints=constraints_text,
        )
        raw = await self._complete_with_retry(
            system=_GENERATE_SYSTEM,
            user=prompt,
            label="scenario generation",
        )
        items = _parse_json(raw)
        if not isinstance(items, list):
            logger.error("Expected JSON array from scenario generation, got %s", type(items))
            return []

        scenarios: list[Scenario] = []
        for item in items[:n]:
            try:
                s = self._build_scenario(item, env, primary_role)
                scenarios.append(s)
            except Exception as exc:
                logger.warning("Skipping malformed scenario: %s", exc)

        logger.info("Generated %d/%d scenarios for '%s'", len(scenarios), n, env.name)
        return scenarios

    # ------------------------------------------------------------------
    # LLMBackend call — same pattern as SessionProcessor
    # ------------------------------------------------------------------

    async def _complete(self, system: str, user: str) -> str:
        """Collect a full text response from the LLMBackend stream."""
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

    async def _complete_with_retry(
        self,
        system: str,
        user: str,
        label: str,
    ) -> str:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                text = await self._complete(system, user)
                _parse_json(text)  # validate parse-ability
                return text
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("%s attempt %d/%d failed: %s", label, attempt, _MAX_RETRIES, exc)
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY * attempt)
        logger.error("%s failed after %d attempts", label, _MAX_RETRIES)
        return "{}" if label == "environment design" else "[]"

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------

    def _build_environment(self, data: dict[str, Any], context: str) -> SimEnvironment:
        personas = [
            PersonaTemplate(
                role=p["role"],
                background=p.get("background", ""),
                personality_traits=p.get("personality_traits", []),
                goals=p.get("goals", []),
                default_frustration=float(p.get("default_frustration", 0.3)),
            )
            for p in data.get("personas", [])
        ]
        tools = [
            ToolSpec(
                name=t["name"],
                description=t.get("description", ""),
                parameters=t.get("parameters", {"type": "object", "properties": {}}),
            )
            for t in data.get("tools", [])
        ]
        raw_criteria = data.get("evaluation_criteria", [])
        raw_weights = [float(c.get("weight", 0.25)) for c in raw_criteria]
        total_weight = sum(raw_weights) or 1.0
        if abs(total_weight - 1.0) > 0.01:
            raw_weights = [w / total_weight for w in raw_weights]

        criteria = [
            EvaluationCriterion(
                name=c["name"],
                description=c.get("description", ""),
                weight=min(1.0, max(0.0, raw_weights[i])),
                rubric=c.get("rubric", {}),
            )
            for i, c in enumerate(raw_criteria)
        ]

        return SimEnvironment(
            name=data.get("name", "Unnamed Environment"),
            description=data.get("description", ""),
            context=context,
            personas=personas,
            tools=tools,
            evaluation_criteria=criteria,
            constraints=data.get("constraints", []),
            workflow_rules=data.get("workflow_rules", []),
            agent_system_prompt=data.get("agent_system_prompt", ""),
            domain_hint=data.get("domain_hint", ""),
        )

    def _build_scenario(
        self,
        item: dict[str, Any],
        env: SimEnvironment,
        primary_role: str,
    ) -> Scenario:
        opening = item.get("opening_message", "Hello, I need help.")
        persona_det = item.get("persona_details", {})
        expected_raw = item.get("expected_outcome", "resolved").lower()
        if expected_raw not in (OutcomeStatus.RESOLVED, OutcomeStatus.ESCALATED, OutcomeStatus.ABANDONED):
            expected_raw = OutcomeStatus.RESOLVED

        initial_ctx: dict[str, Any] = dict(item.get("initial_context", {}))
        if persona_det:
            initial_ctx["persona"] = persona_det

        return Scenario(
            metadata=ScenarioMetadata(
                scenario_id=str(uuid.uuid4()),
                difficulty=item.get("difficulty", "medium"),
                tags=item.get("tags", []),
                generated=True,
            ),
            title=item.get("title", "Untitled Scenario"),
            description=item.get("description", ""),
            system_prompt=env.agent_system_prompt,
            initial_context=initial_ctx,
            turns=[Turn(role=primary_role, content=opening)],
            primary_persona_role=primary_role,
            expected_outcome=expected_raw,
            evaluation_criteria=env.evaluation_criteria,
            constraints=item.get("constraints_in_play", env.constraints),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json(raw: str) -> Any:
    """Strip markdown fences and parse JSON."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        # Take the content between the first pair of fences
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def _primary_persona_role(env: SimEnvironment) -> str:
    """Return the non-agent primary persona role from the environment."""
    agent_like = {"agent", "support_agent", "hr_agent", "sales_rep", "it_agent", "finance_agent"}
    for p in env.personas:
        if p.role.lower() not in agent_like:
            return p.role
    return env.personas[0].role if env.personas else "user"
