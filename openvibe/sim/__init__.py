"""Enterprise Workflow Environment Simulator for openvibe.

Fully dynamic: no hardcoded domains. The LLM designs the environment
from any plain-text context description.

Quick start::

    from openvibe.llm import LiteLLMBackend
    from openvibe.sim import SimHarness, HarnessConfig

    harness = SimHarness(
        llm=LiteLLMBackend(),
        config=HarnessConfig(
            context="Customer support for a SaaS billing platform",
            n_generate=20,
        ),
    )
    report = await harness.run()
    print(report.to_markdown())

Bring your own scenarios::

    report = await harness.run(scenarios=my_scenarios, env=my_env)

Load a saved dataset::

    from openvibe.sim import Dataset
    ds = Dataset.load("dataset.jsonl")
    report = await harness.evaluate_dataset(ds, env=ds.metadata.environment)

Plug into an existing openvibe session (shares the session's LLM backend)::

    harness = SimHarness(llm=session.llm, config=config)
"""

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
from openvibe.sim.designer import SimDesigner
from openvibe.sim.world import WorldSimulator
from openvibe.sim.evaluator import Evaluator
from openvibe.sim.harness import HarnessConfig, SimHarness, run_simulation

__all__ = [
    # Models
    "CriterionScore",
    "Dataset",
    "DatasetMetadata",
    "EvaluationCriterion",
    "EvaluationReport",
    "EvaluationResult",
    "OutcomeStatus",
    "PersonaTemplate",
    "Scenario",
    "ScenarioMetadata",
    "SimEnvironment",
    "SimulationResult",
    "ToolSpec",
    "Turn",
    # Components
    "SimDesigner",
    "WorldSimulator",
    "Evaluator",
    # Harness
    "HarnessConfig",
    "SimHarness",
    "run_simulation",
]
