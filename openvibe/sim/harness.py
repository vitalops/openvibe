"""SimHarness — end-to-end orchestrator for simulation, dataset generation, and evaluation.

Accepts openvibe's ``LLMBackend`` directly — the same instance the session
is already using. No secondary LLM wrappers, no abstraction leakage.

Workflow
--------
1. ``design``    — SimDesigner builds a SimEnvironment from a context string
2. ``generate``  — SimDesigner generates N scenarios for that environment
3. ``simulate``  — WorldSimulator runs each scenario as a multi-turn conversation
4. ``evaluate``  — Evaluator grades each run with an LLM judge
5. ``report``    — EvaluationReport aggregates all scores

Everything is driven by a free-text ``context`` string supplied by the user
(or auto-detected from the session). No domain enum, no hardcoded classes.

Enterprise integration
----------------------
- Pass ``llm=LiteLLMBackend()`` for standalone use
- Pass the session's existing LLM backend to share the provider connection
- ``on_progress`` callback for streaming progress to TUI/CI pipelines
- Output is saved as JSONL (dataset) + JSON (report) to ``output_dir``
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from openvibe.llm import LLMBackend, LiteLLMBackend, resolve_model

from openvibe.sim.designer import SimDesigner
from openvibe.sim.evaluator import Evaluator
from openvibe.sim.scenario import (
    Dataset,
    DatasetMetadata,
    EvaluationReport,
    EvaluationResult,
    Scenario,
    ScenarioMetadata,
    SimEnvironment,
    SimulationResult,
)
from openvibe.sim.world import WorldSimulator

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int], None]  # (message, current, total)


@dataclass
class HarnessConfig:
    """Configuration for one simulation run.

    All that's required is a context string describing what to simulate.
    The LLM backend, model, and all environment components are derived
    from that context — no domain enum, no hardcoded classes.
    """

    context: str = ""
    """Free-text description of what to simulate.
    Examples:
      'Customer support for a SaaS billing platform'
      'HR onboarding at a 500-person tech company'
      '' → LLM infers a sensible enterprise workflow
    """

    model: str = ""
    """litellm model string (e.g. 'anthropic/claude-sonnet-4-6').
    Defaults to the active openvibe config model via resolve_model().
    """

    n_generate: int = 10
    """Number of scenarios to generate if none are provided."""

    max_steps: int = 8
    """Maximum conversation turns per scenario."""

    max_concurrency: int = 3
    """Parallel simulation/evaluation tasks."""

    output_dir: Optional[str] = None
    """If set, saves dataset.jsonl and report.json here."""

    agent_name: str = "openvibe-agent"
    dataset_name: Optional[str] = None
    tags: list[str] = field(default_factory=list)


class SimHarness:
    """End-to-end simulation harness.

    Usage (standalone)::

        from openvibe.llm import LiteLLMBackend
        harness = SimHarness(
            llm=LiteLLMBackend(),
            config=HarnessConfig(context="B2B SaaS customer support"),
        )
        report = await harness.run()
        print(report.to_markdown())

    Usage (inside an openvibe session — reuses the session's LLM backend)::

        harness = SimHarness(llm=session_llm, config=config)
        report = await harness.run()

    With user-supplied scenarios::

        report = await harness.run(scenarios=my_scenarios, env=my_env)

    Load and evaluate a saved dataset::

        ds = Dataset.load("path/to/dataset.jsonl")
        report = await harness.run(dataset=ds, env=ds.metadata.environment)
    """

    def __init__(
        self,
        config: HarnessConfig,
        llm: LLMBackend | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._config = config
        self._on_progress = on_progress or _noop_progress

        # Resolve model and backend
        model = config.model or resolve_model()
        self._model = model
        self._llm: LLMBackend = llm or LiteLLMBackend()

        # Build components — all share the same LLMBackend
        self._designer = SimDesigner(self._llm, model)
        self._world = WorldSimulator(self._llm, model, max_steps=config.max_steps)
        self._evaluator = Evaluator(
            self._llm,
            model,
            agent_name=config.agent_name,
            max_concurrency=config.max_concurrency,
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        scenarios: list[Scenario] | None = None,
        env: SimEnvironment | None = None,
        dataset: Dataset | None = None,
        abort: asyncio.Event | None = None,
    ) -> EvaluationReport:
        """Run the full pipeline: design → generate → simulate → evaluate → report."""
        start = time.time()
        abort = abort or asyncio.Event()

        # ── 1. Environment + dataset ──────────────────────────────────────
        self._on_progress("Designing simulation environment…", 0, 4)

        if dataset is not None:
            ds = dataset
            resolved_env = env or ds.metadata.environment
            if resolved_env is None:
                raise ValueError(
                    "Dataset has no embedded environment. Pass env= explicitly."
                )
            logger.info("Using provided dataset: %d scenarios", len(ds.scenarios))

        elif scenarios is not None:
            resolved_env = env or await self._designer.design(self._config.context)
            ds = _wrap_scenarios(scenarios, resolved_env, self._config)
            logger.info("Using %d provided scenarios", len(ds.scenarios))

        else:
            # Fully autonomous: design + generate
            resolved_env = await self._designer.design(self._config.context)
            self._on_progress(
                f"Generating {self._config.n_generate} scenarios…", 1, 4
            )
            generated = await self._designer.generate_scenarios(
                resolved_env, self._config.n_generate
            )
            ds = _wrap_scenarios(generated, resolved_env, self._config)
            logger.info("Generated %d scenarios", len(ds.scenarios))

        # Save dataset
        if self._config.output_dir:
            output_dir = Path(self._config.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            ds_path = output_dir / f"{ds.metadata.name}.jsonl"
            ds.save(ds_path)
            logger.info("Dataset saved → %s", ds_path)

        # ── 2. Simulate ───────────────────────────────────────────────────
        self._on_progress(f"Simulating {len(ds.scenarios)} scenarios…", 2, 4)
        sim_results = await self._simulate_all(ds.scenarios, resolved_env, abort)

        # ── 3. Evaluate ───────────────────────────────────────────────────
        self._on_progress("Evaluating results…", 3, 4)
        pairs = [
            (scenario, result)
            for scenario, result in zip(ds.scenarios, sim_results)
            if result is not None
        ]
        eval_results = await self._evaluator.evaluate_batch(pairs)

        # ── 4. Report ─────────────────────────────────────────────────────
        report = EvaluationReport.from_results(eval_results, ds)

        if self._config.output_dir:
            report_path = Path(self._config.output_dir) / f"{ds.metadata.name}_report.json"
            report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
            logger.info("Report saved → %s", report_path)

        self._on_progress("Done.", 4, 4)
        elapsed = time.time() - start
        logger.info(
            "Harness complete in %.1fs — score=%.3f pass_rate=%.1f%%",
            elapsed,
            report.mean_total_score,
            report.pass_rate * 100,
        )
        return report

    # ------------------------------------------------------------------
    # Convenience shortcuts
    # ------------------------------------------------------------------

    async def design(self) -> SimEnvironment:
        """Design an environment from the configured context."""
        return await self._designer.design(self._config.context)

    async def generate_dataset(
        self, env: SimEnvironment, n: int | None = None
    ) -> Dataset:
        """Generate a dataset without simulating or evaluating."""
        n = n or self._config.n_generate
        scenarios = await self._designer.generate_scenarios(env, n)
        return _wrap_scenarios(scenarios, env, self._config)

    async def evaluate_dataset(
        self, dataset: Dataset, env: SimEnvironment
    ) -> EvaluationReport:
        """Simulate and evaluate an existing dataset."""
        return await self.run(dataset=dataset, env=env)

    # ------------------------------------------------------------------
    # Simulation with concurrency control
    # ------------------------------------------------------------------

    async def _simulate_all(
        self,
        scenarios: list[Scenario],
        env: SimEnvironment,
        abort: asyncio.Event,
    ) -> list[SimulationResult | None]:
        sem = asyncio.Semaphore(self._config.max_concurrency)

        async def _one(s: Scenario) -> SimulationResult | None:
            async with sem:
                if abort.is_set():
                    return None
                try:
                    return await self._world.simulate(s, env, abort)
                except Exception as exc:
                    logger.error("Simulation failed for '%s': %s", s.title, exc)
                    return None

        return list(await asyncio.gather(*[_one(s) for s in scenarios]))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wrap_scenarios(
    scenarios: list[Scenario],
    env: SimEnvironment,
    config: HarnessConfig,
) -> Dataset:
    name = config.dataset_name or f"{env.name.lower().replace(' ', '_')}_{int(time.time())}"
    return Dataset(
        metadata=DatasetMetadata(
            name=name,
            description=env.description,
            context=config.context,
            environment=env,
            designer_model=config.model,
            tags=config.tags,
        ),
        scenarios=scenarios,
    )


def _noop_progress(msg: str, current: int, total: int) -> None:
    pass


# ---------------------------------------------------------------------------
# Sync wrapper (for CLI / scripts)
# ---------------------------------------------------------------------------


def run_simulation(
    context: str = "",
    model: str = "",
    n: int = 10,
    output_dir: str | None = None,
    llm: LLMBackend | None = None,
    on_progress: ProgressCallback | None = None,
) -> EvaluationReport:
    """Blocking wrapper around :meth:`SimHarness.run` for synchronous use."""
    config = HarnessConfig(context=context, model=model, n_generate=n, output_dir=output_dir)
    harness = SimHarness(config=config, llm=llm, on_progress=on_progress)
    return asyncio.run(harness.run())
