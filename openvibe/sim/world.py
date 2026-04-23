"""WorldSimulator — tick-based conversation simulator using openvibe's LLMBackend.

Each tick:
  1. Agent responds (LLM call via LLMBackend.stream, same pattern as SessionProcessor)
  2. Primary persona responds (LLM call)
  3. Check for termination signals

Tool calls are simulated deterministically from the ToolSpec definitions —
no real side effects, realistic synthetic responses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from openvibe.llm import LLMBackend, Message, StreamDone, TextDelta

from openvibe.sim.scenario import (
    OutcomeStatus,
    Scenario,
    SimEnvironment,
    SimulationResult,
    ToolSpec,
    Turn,
)

logger = logging.getLogger(__name__)

_RESOLVE_SIGNALS = {"[RESOLVED]", "[TICKET_CLOSED]", "[ISSUE_RESOLVED]", "[CASE_CLOSED]", "[DONE]"}
_ESCALATE_SIGNALS = {"[ESCALATED]", "[ESCALATING]", "[MANAGER_NEEDED]", "[ESCALATE]"}
_ABANDON_SIGNALS = {"[ABANDONED]", "[CUSTOMER_LEFT]", "[USER_DISCONNECTED]", "[END]"}

_MAX_STEPS_DEFAULT = 10


class WorldSimulator:
    """Simulates a multi-turn enterprise conversation using openvibe's LLMBackend."""

    def __init__(
        self,
        llm: LLMBackend,
        model: str,
        max_steps: int = _MAX_STEPS_DEFAULT,
    ) -> None:
        self._llm = llm
        self._model = model
        self._max_steps = max_steps

    async def simulate(
        self,
        scenario: Scenario,
        env: SimEnvironment,
        abort: asyncio.Event | None = None,
    ) -> SimulationResult:
        """Run a full multi-turn simulation of *scenario*."""
        start = time.time()
        abort = abort or asyncio.Event()
        conversation: list[Turn] = list(scenario.turns)
        agent_turns: list[Turn] = []
        tool_map = {t.name: t for t in env.tools}
        outcome = OutcomeStatus.PENDING
        step = 0

        try:
            while step < self._max_steps and not abort.is_set():
                step += 1

                # ── Agent turn ────────────────────────────────────────────
                agent_text = await self._agent_turn(scenario, env, conversation, tool_map)
                agent_turn = Turn(role="agent", content=agent_text)
                conversation.append(agent_turn)
                agent_turns.append(agent_turn)
                logger.debug("[step %d] agent: %s", step, agent_text[:80])

                outcome = _detect_outcome(agent_text)
                if outcome != OutcomeStatus.PENDING:
                    break

                # ── Persona turn ──────────────────────────────────────────
                persona_text = await self._persona_turn(scenario, env, conversation)
                persona_role = scenario.primary_persona_role or "user"
                conversation.append(Turn(role=persona_role, content=persona_text))
                logger.debug("[step %d] persona: %s", step, persona_text[:80])

                if any(sig in persona_text.upper() for sig in _ABANDON_SIGNALS):
                    outcome = OutcomeStatus.ABANDONED
                    break

        except asyncio.CancelledError:
            return SimulationResult(
                scenario_id=scenario.metadata.scenario_id,
                agent_turns=agent_turns,
                full_conversation=conversation,
                actual_outcome=OutcomeStatus.PENDING,
                steps_taken=step,
                elapsed_ms=int((time.time() - start) * 1000),
                aborted=True,
            )
        except Exception as exc:
            logger.error("Simulation error: %s", exc, exc_info=True)
            return SimulationResult(
                scenario_id=scenario.metadata.scenario_id,
                agent_turns=agent_turns,
                full_conversation=conversation,
                actual_outcome=OutcomeStatus.PENDING,
                steps_taken=step,
                elapsed_ms=int((time.time() - start) * 1000),
                error=str(exc),
            )

        if outcome == OutcomeStatus.PENDING:
            outcome = OutcomeStatus.ABANDONED  # max steps hit

        return SimulationResult(
            scenario_id=scenario.metadata.scenario_id,
            agent_turns=agent_turns,
            full_conversation=conversation,
            actual_outcome=outcome,
            steps_taken=step,
            elapsed_ms=int((time.time() - start) * 1000),
        )

    # ------------------------------------------------------------------
    # Agent turn
    # ------------------------------------------------------------------

    async def _agent_turn(
        self,
        scenario: Scenario,
        env: SimEnvironment,
        conversation: list[Turn],
        tool_map: dict[str, ToolSpec],
    ) -> str:
        tools_text = _format_tool_list(list(tool_map.values()))
        system = (
            scenario.system_prompt + "\n\n"
            "You have access to these tools. Call them by writing: "
            "TOOL_CALL: tool_name({\"param\": \"value\"})\n\n"
            f"Tools:\n{tools_text}\n\n"
            "When you have fully resolved the situation, end your final message with [RESOLVED].\n"
            "When escalation is required, end with [ESCALATED]."
        )
        messages = _to_messages(conversation, agent_perspective=True)
        raw = await self._complete(system, messages)

        # If the agent issued tool calls, invoke real callables or synthesise results
        if "TOOL_CALL:" in raw:
            raw = await _process_tool_calls(raw, tool_map)

        return raw.strip()

    # ------------------------------------------------------------------
    # Persona turn
    # ------------------------------------------------------------------

    async def _persona_turn(
        self,
        scenario: Scenario,
        env: SimEnvironment,
        conversation: list[Turn],
    ) -> str:
        primary_role = scenario.primary_persona_role or "user"
        persona_tmpl = next(
            (p for p in env.personas if p.role == primary_role),
            env.personas[0] if env.personas else None,
        )

        # Build persona context from initial_context if scenario has persona_details
        persona_ctx = scenario.initial_context.get("persona", {})
        name = persona_ctx.get("name", primary_role.capitalize())
        background = persona_ctx.get("background", persona_tmpl.background if persona_tmpl else "")
        specific_issue = persona_ctx.get("specific_issue", "")
        frustration = persona_ctx.get("frustration_level", persona_tmpl.default_frustration if persona_tmpl else 0.3)
        traits = persona_tmpl.personality_traits if persona_tmpl else []
        goals = persona_tmpl.goals if persona_tmpl else []

        system = (
            f"You are {name}, a {primary_role} in an enterprise interaction.\n"
            f"Background: {background}\n"
            + (f"Your specific issue: {specific_issue}\n" if specific_issue else "")
            + f"Personality: {', '.join(traits)}\n"
            f"Goals: {', '.join(goals)}\n"
            f"Frustration level: {frustration:.1f}/1.0\n\n"
            "Respond naturally and in-character to the agent's last message. "
            "Keep responses concise (1-4 sentences). Do not break character.\n"
            "If you are satisfied and the issue is resolved, include [RESOLVED] at the end.\n"
            "If you want to give up and leave, include [ABANDONED] at the end."
        )
        messages = _to_messages(conversation, agent_perspective=False)
        return (await self._complete(system, messages)).strip()

    # ------------------------------------------------------------------
    # LLMBackend call — same pattern as SessionProcessor
    # ------------------------------------------------------------------

    async def _complete(self, system: str, messages: list[Message]) -> str:
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


def _detect_outcome(text: str) -> str:
    upper = text.upper()
    if any(sig in upper for sig in _RESOLVE_SIGNALS):
        return OutcomeStatus.RESOLVED
    if any(sig in upper for sig in _ESCALATE_SIGNALS):
        return OutcomeStatus.ESCALATED
    if any(sig in upper for sig in _ABANDON_SIGNALS):
        return OutcomeStatus.ABANDONED
    return OutcomeStatus.PENDING


def _to_messages(turns: list[Turn], *, agent_perspective: bool) -> list[Message]:
    """Convert Turn list to LLMBackend Message list."""
    msgs: list[Message] = []
    for t in turns:
        if agent_perspective:
            role = "assistant" if t.role == "agent" else "user"
        else:
            # From persona perspective: persona is assistant, agent is user
            role = "user" if t.role == "agent" else "assistant"
        msgs.append(Message(role=role, content=t.content))
    return msgs


def _format_tool_list(tools: list[ToolSpec]) -> str:
    lines: list[str] = []
    for t in tools:
        props = t.parameters.get("properties", {})
        param_str = ", ".join(
            f"{k}: {v.get('type', 'any')}" for k, v in props.items()
        )
        lines.append(f"- {t.name}({param_str}): {t.description}")
    return "\n".join(lines)


async def _process_tool_calls(raw: str, tool_map: dict[str, ToolSpec]) -> str:
    """Parse TOOL_CALL lines, invoke real callables or synthesise results."""
    lines = raw.split("\n")
    result_lines: list[str] = []
    tool_results: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("TOOL_CALL:"):
            call_text = stripped[len("TOOL_CALL:"):].strip()
            result = await _invoke_tool(call_text, tool_map)
            tool_results.append(f"→ {call_text[:50]}: {result}")
            result_lines.append(f"[Tool called: {call_text.split('(')[0]}]")
        else:
            result_lines.append(line)

    if tool_results:
        result_lines.append("\n[Tool results]\n" + "\n".join(tool_results))

    return "\n".join(result_lines)


async def _invoke_tool(call_text: str, tool_map: dict[str, ToolSpec]) -> str:
    """Invoke a real callable if present, otherwise return a synthetic result."""
    try:
        paren = call_text.index("(")
        tool_name = call_text[:paren].strip()
        args_raw = call_text[paren + 1:].rstrip(")")
        args: dict[str, Any] = json.loads(args_raw) if args_raw.strip() else {}
    except (ValueError, json.JSONDecodeError):
        tool_name = call_text.split("(")[0].strip()
        args = {}

    spec = tool_map.get(tool_name)
    if not spec:
        return f"Error: tool '{tool_name}' not found in environment."

    if spec.callable is not None:
        try:
            result = spec.callable(**args)
            if asyncio.iscoroutine(result):
                result = await result
            return str(result)
        except Exception as exc:
            return f"Error calling {tool_name}: {exc}"

    arg_summary = ", ".join(f"{k}={repr(v)}" for k, v in list(args.items())[:3])
    return f"Success ({arg_summary}). {spec.description.rstrip('.')} completed."
