"""Tests for the Enterprise Workflow Simulation Harness.

All LLM calls go through stub implementations of LLMBackend — exactly
how openvibe's own session processor tests work.  No raw litellm calls.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from openvibe.llm import LLMBackend, Message, StreamDone, TextDelta, ToolDefinition
from openvibe.sim.scenario import (
    CriterionScore,
    Dataset,
    DatasetMetadata,
    EvaluationCriterion,
    EvaluationReport,
    EvaluationResult,
    OutcomeStatus,
    PersonaTemplate,
    Scenario,
    ScenarioMetadata,
    SimEnvironment,
    SimulationResult,
    ToolSpec,
    Turn,
)
from openvibe.sim.designer import SimDesigner, _parse_json, _primary_persona_role
from openvibe.sim.world import WorldSimulator, _detect_outcome, _format_tool_list, _to_messages
from openvibe.sim.evaluator import Evaluator
from openvibe.sim.harness import HarnessConfig, SimHarness


# ---------------------------------------------------------------------------
# Stub LLMBackend — follows openvibe's LLMBackend protocol exactly
# ---------------------------------------------------------------------------


class StubLLMBackend:
    """Stub that returns a fixed reply as a stream of TextDelta + StreamDone."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def stream(
        self,
        model: str,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator:
        self.calls.append({"model": model, "system": system, "messages": messages})
        yield TextDelta(content=self.reply)
        yield StreamDone()


class RoutingStubBackend:
    """Stub that routes between different replies based on system prompt content."""

    def __init__(self, routes: dict[str, str], default: str = "") -> None:
        self.routes = routes  # keyword → reply
        self.default = default
        self.calls: list[str] = []

    async def stream(
        self,
        model: str,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator:
        self.calls.append(system or "")
        reply = self.default
        for keyword, r in self.routes.items():
            if keyword.lower() in (system or "").lower():
                reply = r
                break
        yield TextDelta(content=reply)
        yield StreamDone()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _env() -> SimEnvironment:
    return SimEnvironment(
        name="Test Environment",
        description="A test enterprise workflow.",
        context="Test context",
        personas=[
            PersonaTemplate(
                role="customer",
                background="A customer with a billing problem.",
                personality_traits=["polite"],
                goals=["Get refund"],
                default_frustration=0.3,
            ),
            PersonaTemplate(
                role="agent",
                background="A support agent.",
                personality_traits=["helpful"],
                goals=["Resolve ticket"],
                default_frustration=0.0,
            ),
        ],
        tools=[
            ToolSpec(
                name="lookup_account",
                description="Look up a customer account.",
                parameters={"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]},
            ),
            ToolSpec(
                name="issue_refund",
                description="Issue a refund.",
                parameters={"type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]},
            ),
        ],
        evaluation_criteria=[
            EvaluationCriterion(
                name="resolution",
                description="Did the agent resolve the issue?",
                weight=0.6,
                rubric={"0.0-0.5": "Poor", "0.5-1.0": "Good"},
            ),
            EvaluationCriterion(
                name="efficiency",
                description="Was the agent efficient?",
                weight=0.4,
                rubric={"0.0-0.5": "Slow", "0.5-1.0": "Fast"},
            ),
        ],
        constraints=["Always verify identity first.", "Refunds over $500 need approval."],
        workflow_rules=["Greet the customer.", "Use tools before answering."],
        agent_system_prompt="You are a support agent. Resolve issues professionally.",
        domain_hint="customer support",
    )


def _scenario(env: SimEnvironment | None = None) -> Scenario:
    e = env or _env()
    return Scenario(
        metadata=ScenarioMetadata(
            scenario_id=str(uuid.uuid4()),
            difficulty="medium",
            generated=True,
        ),
        title="Billing dispute",
        description="Customer wants a refund.",
        system_prompt=e.agent_system_prompt,
        initial_context={"persona": {"name": "Alice", "frustration_level": 0.4}},
        turns=[Turn(role="customer", content="I want a refund for my last charge.")],
        primary_persona_role="customer",
        expected_outcome=OutcomeStatus.RESOLVED,
        evaluation_criteria=e.evaluation_criteria,
        constraints=e.constraints,
    )


def _eval_result(s: Scenario, score: float = 0.8) -> EvaluationResult:
    return EvaluationResult(
        scenario_id=s.metadata.scenario_id,
        scenario_title=s.title,
        difficulty=s.metadata.difficulty,
        agent_name="test-agent",
        model="test-model",
        criterion_scores=[
            CriterionScore(criterion="resolution", score=score, weighted_score=score * 0.6, rationale="OK."),
            CriterionScore(criterion="efficiency", score=score, weighted_score=score * 0.4, rationale="OK."),
        ],
        total_score=score,
        outcome_match=True,
        feedback="Agent performed well.",
        improvement_suggestions=["Be more empathetic."],
    )


def _dataset(n: int = 3) -> Dataset:
    e = _env()
    return Dataset(
        metadata=DatasetMetadata(
            name="test_dataset",
            description="Test",
            context="Test context",
            environment=e,
        ),
        scenarios=[_scenario(e) for _ in range(n)],
    )


# ---------------------------------------------------------------------------
# Scenario model tests
# ---------------------------------------------------------------------------


class TestScenarioModels:
    def test_turn_defaults(self):
        t = Turn(role="customer", content="Hello")
        assert t.role == "customer"
        assert isinstance(t.timestamp, float)

    def test_scenario_roundtrip(self):
        s = _scenario()
        line = s.to_jsonl_line()
        s2 = Scenario.from_jsonl_line(line)
        assert s2.title == s.title
        assert s2.metadata.scenario_id == s.metadata.scenario_id

    def test_criterion_weight_bounds(self):
        with pytest.raises(Exception):
            EvaluationCriterion(name="x", description="x", weight=1.5, rubric={})

    def test_persona_frustration_bounds(self):
        with pytest.raises(Exception):
            PersonaTemplate(role="x", background="x", default_frustration=2.0)

    def test_outcome_status_constants(self):
        assert OutcomeStatus.RESOLVED == "resolved"
        assert OutcomeStatus.ESCALATED == "escalated"
        assert OutcomeStatus.ABANDONED == "abandoned"

    def test_tool_spec_default_params(self):
        t = ToolSpec(name="foo", description="bar")
        assert "type" in t.parameters


class TestSimEnvironment:
    def test_env_has_required_fields(self):
        e = _env()
        assert e.name
        assert len(e.personas) >= 1
        assert len(e.tools) >= 1
        assert len(e.evaluation_criteria) >= 1
        assert abs(sum(c.weight for c in e.evaluation_criteria) - 1.0) < 0.01

    def test_env_serializes(self):
        e = _env()
        data = e.model_dump()
        e2 = SimEnvironment.model_validate(data)
        assert e2.name == e.name
        assert len(e2.tools) == len(e.tools)


class TestDataset:
    def test_save_load(self, tmp_path: Path):
        ds = _dataset(n=3)
        path = tmp_path / "ds.jsonl"
        ds.save(path)
        loaded = Dataset.load(path)
        assert len(loaded.scenarios) == 3
        assert loaded.metadata.name == ds.metadata.name
        # Environment is embedded and round-trips
        assert loaded.metadata.environment is not None
        assert loaded.metadata.environment.name == ds.metadata.environment.name

    def test_to_jsonl(self):
        ds = _dataset(n=2)
        lines = [l for l in ds.to_jsonl().splitlines() if l.strip()]
        assert len(lines) == 3  # 1 metadata + 2 scenarios

    def test_summary(self):
        ds = _dataset(n=4)
        s = ds.summary()
        assert s["total"] == 4
        assert isinstance(s["by_difficulty"], dict)


# ---------------------------------------------------------------------------
# EvaluationReport tests
# ---------------------------------------------------------------------------


class TestEvaluationReport:
    def test_from_results(self):
        ds = _dataset(n=4)
        results = [_eval_result(s, score=0.8) for s in ds.scenarios]
        report = EvaluationReport.from_results(results, ds)
        assert report.evaluated == 4
        assert abs(report.mean_total_score - 0.8) < 0.01
        assert report.pass_rate == 1.0

    def test_mixed_scores(self):
        ds = _dataset(n=4)
        scores = [0.9, 0.8, 0.5, 0.3]
        results = [_eval_result(s, sc) for s, sc in zip(ds.scenarios, scores)]
        report = EvaluationReport.from_results(results, ds)
        assert report.pass_rate == 0.5
        assert abs(report.mean_total_score - sum(scores) / 4) < 0.01

    def test_to_markdown(self):
        ds = _dataset(n=2)
        results = [_eval_result(s) for s in ds.scenarios]
        report = EvaluationReport.from_results(results, ds)
        md = report.to_markdown()
        assert "# Evaluation Report" in md
        assert "Mean Score" in md
        assert "Pass Rate" in md

    def test_to_dict(self):
        ds = _dataset(n=2)
        results = [_eval_result(s) for s in ds.scenarios]
        report = EvaluationReport.from_results(results, ds)
        d = report.to_dict()
        assert "mean_total_score" in d
        assert "results" in d


# ---------------------------------------------------------------------------
# SimDesigner tests — uses StubLLMBackend (openvibe protocol)
# ---------------------------------------------------------------------------


def _env_json() -> str:
    return json.dumps({
        "name": "Customer Support",
        "description": "A support workflow for SaaS billing.",
        "personas": [
            {"role": "customer", "background": "A billing customer.", "personality_traits": ["polite"], "goals": ["refund"], "default_frustration": 0.3},
            {"role": "agent", "background": "Support agent.", "personality_traits": ["helpful"], "goals": ["resolve"], "default_frustration": 0.0},
        ],
        "tools": [
            {"name": "lookup_account", "description": "Look up account.", "parameters": {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]}},
            {"name": "issue_refund", "description": "Issue refund.", "parameters": {"type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]}},
        ],
        "evaluation_criteria": [
            {"name": "resolution", "description": "Was resolved?", "weight": 0.6, "rubric": {"0.0-0.5": "Poor", "0.5-1.0": "Good"}},
            {"name": "efficiency", "description": "Was efficient?", "weight": 0.4, "rubric": {"0.0-0.5": "Slow", "0.5-1.0": "Fast"}},
        ],
        "constraints": ["Verify identity first.", "Refunds > $500 need approval."],
        "workflow_rules": ["Greet customer.", "Use tools."],
        "agent_system_prompt": "You are a support agent.",
        "domain_hint": "customer support",
    })


def _scenarios_json(n: int = 3) -> str:
    return json.dumps([
        {
            "title": f"Scenario {i}",
            "description": f"Desc {i}.",
            "difficulty": ["easy", "medium", "hard"][i % 3],
            "opening_message": "I need help.",
            "persona_details": {"name": "Alice", "background": "A customer.", "frustration_level": 0.3},
            "expected_outcome": "resolved",
            "initial_context": {},
            "tags": ["billing"],
            "constraints_in_play": [],
        }
        for i in range(n)
    ])


class TestSimDesigner:
    @pytest.mark.asyncio
    async def test_design_uses_llm_backend(self):
        llm = StubLLMBackend(_env_json())
        designer = SimDesigner(llm, model="test/model")
        env = await designer.design("SaaS billing support")
        # Verify LLMBackend.stream() was called (not litellm directly)
        assert len(llm.calls) >= 1
        assert llm.calls[0]["model"] == "test/model"

    @pytest.mark.asyncio
    async def test_design_returns_environment(self):
        llm = StubLLMBackend(_env_json())
        designer = SimDesigner(llm, model="test/model")
        env = await designer.design("customer support")
        assert isinstance(env, SimEnvironment)
        assert env.name == "Customer Support"
        assert len(env.personas) == 2
        assert len(env.tools) == 2
        assert len(env.evaluation_criteria) == 2

    @pytest.mark.asyncio
    async def test_design_normalises_weights(self):
        # Weights don't sum to 1.0 in input
        bad = json.loads(_env_json())
        bad["evaluation_criteria"][0]["weight"] = 2.0
        bad["evaluation_criteria"][1]["weight"] = 2.0
        llm = StubLLMBackend(json.dumps(bad))
        designer = SimDesigner(llm, model="x")
        env = await designer.design("test")
        total = sum(c.weight for c in env.evaluation_criteria)
        assert abs(total - 1.0) < 0.01

    @pytest.mark.asyncio
    async def test_design_handles_empty_context(self):
        llm = StubLLMBackend(_env_json())
        designer = SimDesigner(llm, model="x")
        env = await designer.design("")  # empty → LLM infers
        assert isinstance(env, SimEnvironment)
        assert len(llm.calls) >= 1

    @pytest.mark.asyncio
    async def test_design_retries_on_json_error(self):
        call_count = 0
        valid = _env_json()

        class RetryStub:
            async def stream(self, model, messages, tools=None, system=None, **kw):
                nonlocal call_count
                call_count += 1
                reply = "NOT JSON" if call_count < 3 else valid
                yield TextDelta(content=reply)
                yield StreamDone()

        designer = SimDesigner(RetryStub(), model="x")
        env = await designer.design("test")
        assert isinstance(env, SimEnvironment)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_design_strips_markdown_fences(self):
        fenced = f"```json\n{_env_json()}\n```"
        llm = StubLLMBackend(fenced)
        designer = SimDesigner(llm, model="x")
        env = await designer.design("test")
        assert isinstance(env, SimEnvironment)

    @pytest.mark.asyncio
    async def test_generate_scenarios_returns_list(self):
        llm = StubLLMBackend(_scenarios_json(5))
        designer = SimDesigner(llm, model="x")
        e = _env()
        scenarios = await designer.generate_scenarios(e, n=5)
        assert len(scenarios) == 5
        for s in scenarios:
            assert isinstance(s, Scenario)
            assert s.metadata.generated is True

    @pytest.mark.asyncio
    async def test_generate_scenarios_uses_env_criteria(self):
        llm = StubLLMBackend(_scenarios_json(2))
        designer = SimDesigner(llm, model="x")
        e = _env()
        scenarios = await designer.generate_scenarios(e, n=2)
        for s in scenarios:
            assert s.evaluation_criteria == e.evaluation_criteria

    def test_parse_json_plain(self):
        result = _parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_fenced(self):
        result = _parse_json("```json\n{\"key\": \"value\"}\n```")
        assert result == {"key": "value"}

    def test_parse_json_invalid_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json("not json")

    def test_primary_persona_role(self):
        e = _env()
        role = _primary_persona_role(e)
        assert role == "customer"

    def test_primary_persona_role_no_agent(self):
        e = _env()
        # Remove agent — primary should still be customer
        e.personas = [p for p in e.personas if p.role == "customer"]
        role = _primary_persona_role(e)
        assert role == "customer"


# ---------------------------------------------------------------------------
# WorldSimulator tests — uses StubLLMBackend
# ---------------------------------------------------------------------------


class TestWorldSimulator:
    @pytest.mark.asyncio
    async def test_simulate_resolved(self):
        llm = RoutingStubBackend(
            routes={"support agent": "I've processed your refund. [RESOLVED]"},
            default="Thank you! [RESOLVED]",
        )
        sim = WorldSimulator(llm, model="test/model", max_steps=5)
        e = _env()
        s = _scenario(e)
        result = await sim.simulate(s, e)
        assert isinstance(result, SimulationResult)
        assert result.actual_outcome == OutcomeStatus.RESOLVED
        assert result.steps_taken >= 1
        assert not result.aborted
        assert not result.error

    @pytest.mark.asyncio
    async def test_simulate_escalated(self):
        llm = RoutingStubBackend(
            routes={"support agent": "This needs a manager. [ESCALATED]"},
            default="OK.",
        )
        sim = WorldSimulator(llm, model="x", max_steps=5)
        e = _env()
        s = _scenario(e)
        result = await sim.simulate(s, e)
        assert result.actual_outcome == OutcomeStatus.ESCALATED

    @pytest.mark.asyncio
    async def test_simulate_max_steps_abandoned(self):
        llm = StubLLMBackend("Still looking into it...")
        sim = WorldSimulator(llm, model="x", max_steps=2)
        e = _env()
        s = _scenario(e)
        result = await sim.simulate(s, e)
        assert result.actual_outcome == OutcomeStatus.ABANDONED
        assert result.steps_taken == 2

    @pytest.mark.asyncio
    async def test_simulate_abort(self):
        abort = asyncio.Event()

        class AbortingStub:
            async def stream(self, model, messages, tools=None, system=None, **kw):
                abort.set()
                yield TextDelta(content="Working...")
                yield StreamDone()

        sim = WorldSimulator(AbortingStub(), model="x", max_steps=10)
        e = _env()
        s = _scenario(e)
        result = await sim.simulate(s, e, abort=abort)
        assert result.steps_taken <= 2

    @pytest.mark.asyncio
    async def test_simulate_with_tool_calls(self):
        llm = RoutingStubBackend(
            routes={"support agent": 'TOOL_CALL: lookup_account({"email": "alice@example.com"})\nYour account is verified. [RESOLVED]'},
            default="Thanks!",
        )
        sim = WorldSimulator(llm, model="x", max_steps=5)
        e = _env()
        s = _scenario(e)
        result = await sim.simulate(s, e)
        # Should have processed tool call; simulation completes
        assert isinstance(result, SimulationResult)

    @pytest.mark.asyncio
    async def test_simulate_uses_llm_backend_stream(self):
        """Verify the simulator calls LLMBackend.stream() not litellm directly."""
        llm = StubLLMBackend("Done. [RESOLVED]")
        sim = WorldSimulator(llm, model="anthropic/claude-sonnet-4-6", max_steps=3)
        e = _env()
        s = _scenario(e)
        await sim.simulate(s, e)
        # StubLLMBackend records calls; verify model was passed correctly
        assert len(llm.calls) >= 1
        assert llm.calls[0]["model"] == "anthropic/claude-sonnet-4-6"

    def test_detect_outcome_resolved(self):
        assert _detect_outcome("Here you go. [RESOLVED]") == OutcomeStatus.RESOLVED

    def test_detect_outcome_escalated(self):
        assert _detect_outcome("Going up. [ESCALATED]") == OutcomeStatus.ESCALATED

    def test_detect_outcome_abandoned(self):
        assert _detect_outcome("[ABANDONED]") == OutcomeStatus.ABANDONED

    def test_detect_outcome_pending(self):
        assert _detect_outcome("Let me check.") == OutcomeStatus.PENDING

    def test_format_tool_list(self):
        tools = [ToolSpec(name="foo", description="Does foo.", parameters={"type": "object", "properties": {"x": {"type": "string"}}})]
        text = _format_tool_list(tools)
        assert "foo" in text
        assert "Does foo" in text

    def test_to_messages_agent_perspective(self):
        turns = [
            Turn(role="customer", content="Hi"),
            Turn(role="agent", content="Hello"),
        ]
        msgs = _to_messages(turns, agent_perspective=True)
        assert msgs[0].role == "user"
        assert msgs[1].role == "assistant"

    def test_to_messages_persona_perspective(self):
        turns = [
            Turn(role="customer", content="Hi"),
            Turn(role="agent", content="Hello"),
        ]
        msgs = _to_messages(turns, agent_perspective=False)
        assert msgs[0].role == "assistant"
        assert msgs[1].role == "user"


# ---------------------------------------------------------------------------
# Evaluator tests — uses StubLLMBackend
# ---------------------------------------------------------------------------


def _judge_json(score: float = 0.85) -> str:
    return json.dumps({
        "scores": {
            "resolution": {"score": score, "rationale": "Agent resolved it."},
            "efficiency": {"score": score, "rationale": "Done in 2 steps."},
        },
        "overall_feedback": "Agent did well.",
        "improvement_suggestions": ["Be warmer."],
    })


def _sim_result(scenario: Scenario, outcome: str = OutcomeStatus.RESOLVED) -> SimulationResult:
    return SimulationResult(
        scenario_id=scenario.metadata.scenario_id,
        agent_turns=[Turn(role="agent", content="Refund issued. [RESOLVED]")],
        full_conversation=[
            Turn(role="customer", content="I want a refund."),
            Turn(role="agent", content="Refund issued. [RESOLVED]"),
        ],
        actual_outcome=outcome,
        steps_taken=1,
        elapsed_ms=200,
    )


class TestEvaluator:
    @pytest.mark.asyncio
    async def test_evaluate_uses_llm_backend_stream(self):
        llm = StubLLMBackend(_judge_json(0.9))
        evaluator = Evaluator(llm, model="test/model", agent_name="agent")
        s = _scenario()
        result = await evaluator.evaluate(s, _sim_result(s))
        # Verify stream() was called on the backend
        assert len(llm.calls) >= 1
        assert llm.calls[0]["model"] == "test/model"

    @pytest.mark.asyncio
    async def test_evaluate_returns_result(self):
        llm = StubLLMBackend(_judge_json(0.85))
        evaluator = Evaluator(llm, model="x")
        s = _scenario()
        result = await evaluator.evaluate(s, _sim_result(s))
        assert isinstance(result, EvaluationResult)
        assert 0.0 <= result.total_score <= 1.0
        assert result.scenario_id == s.metadata.scenario_id

    @pytest.mark.asyncio
    async def test_evaluate_outcome_match(self):
        llm = StubLLMBackend(_judge_json(0.8))
        evaluator = Evaluator(llm, model="x")
        s = _scenario()
        result = await evaluator.evaluate(s, _sim_result(s, OutcomeStatus.RESOLVED))
        assert result.outcome_match is True

    @pytest.mark.asyncio
    async def test_evaluate_outcome_mismatch(self):
        llm = StubLLMBackend(_judge_json(0.5))
        evaluator = Evaluator(llm, model="x")
        s = _scenario()  # expected RESOLVED
        result = await evaluator.evaluate(s, _sim_result(s, OutcomeStatus.ESCALATED))
        assert result.outcome_match is False

    @pytest.mark.asyncio
    async def test_evaluate_fallback_on_error(self):
        class ErrorStub:
            async def stream(self, model, messages, tools=None, system=None, **kw):
                raise RuntimeError("LLM down")
                yield  # make it a generator

        evaluator = Evaluator(ErrorStub(), model="x")
        s = _scenario()
        result = await evaluator.evaluate(s, _sim_result(s))
        assert result.total_score == 0.0

    @pytest.mark.asyncio
    async def test_evaluate_strips_markdown_fences(self):
        fenced = f"```json\n{_judge_json(0.7)}\n```"
        llm = StubLLMBackend(fenced)
        evaluator = Evaluator(llm, model="x")
        s = _scenario()
        result = await evaluator.evaluate(s, _sim_result(s))
        assert result.total_score > 0.0

    @pytest.mark.asyncio
    async def test_evaluate_batch(self):
        llm = StubLLMBackend(_judge_json(0.75))
        evaluator = Evaluator(llm, model="x", max_concurrency=2)
        scenarios = [_scenario() for _ in range(4)]
        pairs = [(s, _sim_result(s)) for s in scenarios]
        results = await evaluator.evaluate_batch(pairs)
        assert len(results) == 4
        for r in results:
            assert 0.0 <= r.total_score <= 1.0


# ---------------------------------------------------------------------------
# SimHarness integration tests
# ---------------------------------------------------------------------------


class TestSimHarness:
    def _make_routing_llm(self) -> RoutingStubBackend:
        """Routing stub that handles designer, world, and evaluator calls."""
        return RoutingStubBackend(
            routes={
                # Designer system prompt keywords
                "workflow architect": _env_json(),
                "scenario designer": _scenarios_json(2),
                # Evaluator system prompt keyword
                "expert evaluator": _judge_json(0.8),
                # World agent system prompt
                "support agent": "Your refund is processed. [RESOLVED]",
            },
            default="Thank you! [RESOLVED]",
        )

    @pytest.mark.asyncio
    async def test_run_with_provided_scenarios(self):
        llm = self._make_routing_llm()
        config = HarnessConfig(context="billing support", n_generate=2, max_steps=2)
        harness = SimHarness(config=config, llm=llm)
        e = _env()
        scenarios = [_scenario(e) for _ in range(2)]
        report = await harness.run(scenarios=scenarios, env=e)
        assert isinstance(report, EvaluationReport)
        assert report.evaluated >= 0

    @pytest.mark.asyncio
    async def test_run_with_dataset(self):
        llm = self._make_routing_llm()
        ds = _dataset(n=2)
        config = HarnessConfig(context="billing support", max_steps=2)
        harness = SimHarness(config=config, llm=llm)
        report = await harness.run(dataset=ds, env=ds.metadata.environment)
        assert isinstance(report, EvaluationReport)

    @pytest.mark.asyncio
    async def test_design_shortcut(self):
        llm = RoutingStubBackend(
            routes={"workflow architect": _env_json()},
            default="{}",
        )
        config = HarnessConfig(context="billing support")
        harness = SimHarness(config=config, llm=llm)
        env = await harness.design()
        assert isinstance(env, SimEnvironment)
        assert env.name == "Customer Support"

    @pytest.mark.asyncio
    async def test_generate_dataset_shortcut(self):
        llm = RoutingStubBackend(
            routes={"scenario designer": _scenarios_json(3)},
            default="[]",
        )
        config = HarnessConfig(context="billing support", n_generate=3)
        harness = SimHarness(config=config, llm=llm)
        e = _env()
        ds = await harness.generate_dataset(e, n=3)
        assert isinstance(ds, Dataset)
        assert ds.metadata.environment is not None

    @pytest.mark.asyncio
    async def test_save_dataset_to_disk(self, tmp_path: Path):
        llm = self._make_routing_llm()
        config = HarnessConfig(context="billing", max_steps=2, output_dir=str(tmp_path))
        harness = SimHarness(config=config, llm=llm)
        e = _env()
        scenarios = [_scenario(e) for _ in range(2)]
        await harness.run(scenarios=scenarios, env=e)
        # At least the report JSON should be saved
        json_files = list(tmp_path.glob("*.json"))
        assert len(json_files) >= 1

    @pytest.mark.asyncio
    async def test_all_components_use_same_llm_backend(self):
        """All three components (designer, world, evaluator) call the same backend."""
        llm = self._make_routing_llm()
        config = HarnessConfig(context="test", n_generate=1, max_steps=2)
        harness = SimHarness(config=config, llm=llm)
        # Verify all three internal components reference the same llm instance
        assert harness._designer._llm is llm
        assert harness._world._llm is llm
        assert harness._evaluator._llm is llm

    @pytest.mark.asyncio
    async def test_run_missing_env_raises(self):
        """Dataset without embedded env requires explicit env= argument."""
        llm = StubLLMBackend("{}")
        ds = Dataset(
            metadata=DatasetMetadata(name="x", context="x", environment=None),
            scenarios=[],
        )
        harness = SimHarness(config=HarnessConfig(), llm=llm)
        with pytest.raises(ValueError):
            await harness.run(dataset=ds)


# ---------------------------------------------------------------------------
# Command tests
# ---------------------------------------------------------------------------


class TestSimCommands:
    def test_sim_command_shows_subcommands(self):
        from openvibe.commands import execute, CommandContext
        ctx = CommandContext(session=MagicMock(), args="")
        result = execute("sim", ctx)
        assert "run" in result.output
        assert "generate" in result.output
        assert "design" in result.output
        assert "load" in result.output

    def test_sim_command_no_domain_enum(self):
        """Verify the /sim command no longer references domain enums."""
        from openvibe.commands import execute, CommandContext
        ctx = CommandContext(session=MagicMock(), args="")
        result = execute("sim", ctx)
        # Should show plain-text context approach, not domain enum list
        assert "customer_support" not in result.output or "plain text" in result.output.lower() or "context" in result.output.lower()

    def test_sim_subcommands_registered(self):
        from openvibe.commands import _COMMANDS
        sim = _COMMANDS.get("sim")
        assert sim is not None
        assert "run" in sim.subcommands
        assert "design" in sim.subcommands
        assert "generate" in sim.subcommands
        assert "load" in sim.subcommands


# ---------------------------------------------------------------------------
# SimTool tests
# ---------------------------------------------------------------------------


class TestSimTool:
    @pytest.mark.asyncio
    async def test_sim_tool_invalid_mode(self):
        from openvibe.tool.sim_tool import SimTool
        from openvibe.tool.base import ToolContext
        from unittest.mock import patch

        tool = SimTool()
        ctx = ToolContext(
            session_id="s1", message_id="m1", agent_name="test",
            project_id="p1", working_dir="/tmp",
        )
        # evaluate mode without output_path should error
        params = SimTool.Params(context="test", mode="evaluate", output_path=None)
        with patch("openvibe.llm.LiteLLMBackend"), \
             patch("openvibe.llm.resolve_model", return_value="test/model"):
            result = await tool.execute(ctx, params)
        assert result.error is True

    @pytest.mark.asyncio
    async def test_sim_tool_design_mode(self):
        from openvibe.tool.sim_tool import SimTool
        from openvibe.tool.base import ToolContext
        from unittest.mock import patch, AsyncMock

        tool = SimTool()
        ctx = ToolContext(
            session_id="s1", message_id="m1", agent_name="test",
            project_id="p1", working_dir="/tmp",
        )
        params = SimTool.Params(context="billing support", mode="design")

        mock_env = _env()

        with patch("openvibe.sim.harness.SimHarness.design", new_callable=AsyncMock, return_value=mock_env), \
             patch("openvibe.sim.harness.LiteLLMBackend"), \
             patch("openvibe.sim.harness.resolve_model", return_value="test/model"):
            result = await tool.execute(ctx, params)

        assert not result.error
        assert "Customer Support" in result.output or "Environment" in result.title
