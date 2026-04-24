"""Simulation tool — exposes the SimHarness to the agent via the tool system.

Uses openvibe's ``LiteLLMBackend`` and ``resolve_model()`` — the same
abstractions the rest of openvibe uses.  No raw litellm calls.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pydantic import BaseModel, Field

from openvibe.tool.base import Tool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class SimTool(Tool):
    """Run a simulation environment to evaluate an agent's output.

    Designs the simulation environment from a plain-text context describing
    the task and what was built, generates scenarios, simulates conversations,
    and evaluates performance — all without leaving the session.
    """

    name = "simulate"
    description = (
        "Run a workflow simulation to evaluate what was built. Provide the task description "
        "and what was built as context; the harness designs an environment, generates scenarios, "
        "simulates multi-turn conversations, and evaluates agent performance automatically."
    )

    class Params(BaseModel):
        context: str = Field(
            default="",
            description=(
                "The original task description and a summary of what was produced. "
                "The harness uses this to design a simulation environment tailored to the output."
            ),
        )
        n_scenarios: int = Field(
            default=5,
            ge=1,
            le=50,
            description="Number of scenarios to generate and evaluate.",
        )
        model: str = Field(
            default="",
            description=(
                "litellm model string (e.g. 'anthropic/claude-sonnet-4-6'). "
                "Defaults to the active openvibe model."
            ),
        )
        output_path: Optional[str] = Field(
            default=None,
            description="Optional directory path to save dataset JSONL and report JSON.",
        )
        mode: str = Field(
            default="full",
            description=(
                "'full' = design → generate → simulate → evaluate; "
                "'design' = only design and show the environment; "
                "'generate' = design + generate dataset (no simulation); "
                "'evaluate' = load dataset from output_path and evaluate."
            ),
        )

    async def execute(self, ctx: ToolContext, params: "SimTool.Params") -> ToolResult:
        try:
            from openvibe.llm import LiteLLMBackend, resolve_model
            from openvibe.sim import (
                Dataset,
                HarnessConfig,
                SimDesigner,
                SimHarness,
            )
        except ImportError as exc:
            return ToolResult(
                title="Import error",
                output=f"Could not import sim module: {exc}",
                error=True,
            )

        model = params.model or resolve_model()
        llm = LiteLLMBackend()

        progress_lines: list[str] = []

        def on_progress(msg: str, current: int, total: int) -> None:
            progress_lines.append(f"[{current}/{total}] {msg}")

        config = HarnessConfig(
            context=params.context,
            model=model,
            n_generate=params.n_scenarios,
            output_dir=params.output_path,
            agent_name=ctx.agent_name,
        )
        harness = SimHarness(config=config, llm=llm, on_progress=on_progress)

        try:
            if params.mode == "design":
                env = await harness.design()
                lines = [
                    f"**Environment: {env.name}**",
                    f"_{env.description}_",
                    "",
                    f"**Personas ({len(env.personas)}):** " + ", ".join(p.role for p in env.personas),
                    f"**Tools ({len(env.tools)}):** " + ", ".join(t.name for t in env.tools),
                    f"**Evaluation criteria:** " + ", ".join(
                        f"{c.name} ({c.weight:.0%})" for c in env.evaluation_criteria
                    ),
                    f"**Constraints:** {len(env.constraints)} rules",
                ]
                return ToolResult(
                    title=f"Environment: {env.name}",
                    output="\n".join(lines + [""] + progress_lines),
                )

            elif params.mode == "generate":
                env = await harness.design()
                ds = await harness.generate_dataset(env, n=params.n_scenarios)
                summary = ds.summary()
                lines = [
                    f"Dataset '{summary['name']}': {summary['total']} scenarios",
                    f"Difficulty breakdown: {summary['by_difficulty']}",
                ]
                if params.output_path:
                    import json as _json
                    from pathlib import Path
                    p = Path(params.output_path)
                    p.mkdir(parents=True, exist_ok=True)
                    ds_path = p / f"{ds.metadata.name}.jsonl"
                    ds.save(ds_path)
                    lines.append(f"Saved → {ds_path}")
                return ToolResult(
                    title=f"Dataset: {ds.metadata.name}",
                    output="\n".join(lines + [""] + progress_lines),
                )

            elif params.mode == "evaluate":
                if not params.output_path:
                    return ToolResult(
                        title="Error",
                        output="'evaluate' mode requires output_path pointing to a saved dataset directory.",
                        error=True,
                    )
                from pathlib import Path
                import glob as _glob
                matches = list(Path(params.output_path).glob("*.jsonl"))
                if not matches:
                    return ToolResult(
                        title="Error",
                        output=f"No .jsonl dataset found in {params.output_path}",
                        error=True,
                    )
                ds = Dataset.load(matches[0])
                env = ds.metadata.environment
                if env is None:
                    return ToolResult(
                        title="Error",
                        output="Dataset has no embedded environment. Regenerate with 'generate' mode first.",
                        error=True,
                    )
                report = await harness.evaluate_dataset(ds, env)

            else:  # full
                report = await harness.run()

            # Save human-readable markdown report alongside the JSON
            if params.output_path:
                from pathlib import Path
                md_path = Path(params.output_path) / f"{report.dataset_name}_report.md"
                md_path.write_text(_build_markdown_report(report), encoding="utf-8")

            # Format summary for tool output
            summary_lines = [
                f"Scenarios evaluated: {report.evaluated}/{report.total_scenarios}",
                f"Mean score: {report.mean_total_score:.3f}",
                f"Pass rate (≥0.7): {report.pass_rate:.1%}",
                f"Outcome accuracy: {report.outcome_accuracy:.1%}",
                "",
                "By difficulty:",
            ]
            order = ["easy", "medium", "hard", "adversarial"]
            for diff, score in sorted(
                report.by_difficulty.items(),
                key=lambda x: order.index(x[0]) if x[0] in order else 99,
            ):
                flag = "✓" if score >= 0.7 else "✗"
                summary_lines.append(f"  {diff}: {score:.3f} {flag}")
            summary_lines += ["", "By criterion:"]
            for crit, score in sorted(report.by_criterion.items(), key=lambda x: -x[1]):
                summary_lines.append(f"  {crit}: {score:.3f}")
            if report.results:
                summary_lines += ["", "Needs improvement:"]
                for r in sorted(report.results, key=lambda x: x.total_score)[:3]:
                    if r.total_score < 0.7:
                        summary_lines.append(f"  ✗ [{r.difficulty}] {r.scenario_title}: {r.total_score:.3f}")
                        if r.improvement_suggestions:
                            summary_lines.append(f"    → {r.improvement_suggestions[0]}")
            if params.output_path:
                summary_lines += ["", f"Full report saved → {params.output_path}/"]

            return ToolResult(
                title=f"Simulation Report: {report.dataset_name}",
                output="\n".join(progress_lines + [""] + summary_lines),
                metadata={
                    "mean_score": report.mean_total_score,
                    "pass_rate": report.pass_rate,
                    "evaluated": report.evaluated,
                },
            )

        except Exception as exc:
            logger.exception("Simulation failed")
            return ToolResult(
                title="Simulation failed",
                output=f"Error: {exc}\n\nProgress:\n" + "\n".join(progress_lines),
                error=True,
            )


def _build_markdown_report(report) -> str:
    order = ["easy", "medium", "hard", "adversarial"]
    lines = [
        f"# Evaluation Report: {report.dataset_name}",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Scenarios evaluated | {report.evaluated} / {report.total_scenarios} |",
        f"| Mean score | {report.mean_total_score:.3f} |",
        f"| Median score | {report.median_total_score:.3f} |",
        f"| Pass rate (≥ 0.7) | {report.pass_rate:.1%} |",
        f"| Outcome accuracy | {report.outcome_accuracy:.1%} |",
        "",
        "## By Difficulty",
        "",
    ]
    for diff, score in sorted(
        report.by_difficulty.items(),
        key=lambda x: order.index(x[0]) if x[0] in order else 99,
    ):
        bar  = "█" * int(score * 20) + "░" * (20 - int(score * 20))
        flag = "✓" if score >= 0.7 else "✗"
        lines.append(f"- **{diff}**: `{bar}` {score:.3f} {flag}")

    lines += ["", "## By Criterion", ""]
    for crit, score in sorted(report.by_criterion.items(), key=lambda x: -x[1]):
        lines.append(f"- **{crit}**: {score:.3f}")

    lines += ["", "## Scenario Results", ""]
    for r in sorted(report.results, key=lambda x: -x.total_score):
        icon = "✓" if r.total_score >= 0.7 else "✗"
        lines.append(f"### {icon} [{r.difficulty}] {r.scenario_title}")
        lines.append(f"**Score**: {r.total_score:.3f} | **Outcome match**: {'yes' if r.outcome_match else 'no'}")
        lines.append("")
        lines.append(f"{r.feedback}")
        if r.criterion_scores:
            lines += ["", "| Criterion | Score | Weighted |", "|-----------|-------|---------|"]
            for cs in r.criterion_scores:
                lines.append(f"| {cs.criterion} | {cs.score:.3f} | {cs.weighted_score:.3f} |")
        if r.improvement_suggestions:
            lines += ["", "**Improvements:**"]
            for s in r.improvement_suggestions:
                lines.append(f"- {s}")
        lines.append("")

    return "\n".join(lines)
