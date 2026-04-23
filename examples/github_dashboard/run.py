#!/usr/bin/env python3
"""
GitHub Analytics Dashboard — build and evaluate pipeline.

Phase 1: openvibe builds the dashboard (fetch.py + server.py + HTML UI).
Phase 2: SimHarness automatically designs an evaluation environment from what
         was built, generates scenarios, runs them, and saves a full report.

Usage:
  python examples/github_dashboard/run.py                   # full pipeline
  python examples/github_dashboard/run.py --phase build     # Phase 1 only
  python examples/github_dashboard/run.py --phase evaluate  # Phase 2 only
  python examples/github_dashboard/run.py --model openai/gpt-4o

TUI:
  build and evaluate examples/github_dashboard/TASK.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_repo_root))

_EXAMPLE_DIR   = Path(__file__).parent
_TASK_FILE     = _EXAMPLE_DIR / "TASK.md"
_SOLUTION_FILE = _EXAMPLE_DIR / "SOLUTION.md"
_OUTPUT_DIR    = _EXAMPLE_DIR / "eval_output"
_SERVER_SCRIPT = _EXAMPLE_DIR / "app" / "server.py"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GitHub dashboard build and evaluate")
    p.add_argument("--phase", choices=["full", "build", "evaluate"], default="full")
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--model", default="")
    p.add_argument("--max-steps", type=int, default=15)
    return p.parse_args()


# ── Phase 1: Build ────────────────────────────────────────────────────────────

def _run_build_phase() -> str:
    from openvibe import OpenVibe, SessionState

    task_text = _TASK_FILE.read_text(encoding="utf-8")

    prompt = f"""Complete the following task using your tools.

{task_text}

Work from the repository root. All created files should live under
`examples/github_dashboard/`. After building:
1. Run `python examples/github_dashboard/app/fetch.py` to populate the database.
2. Start the server briefly and verify each endpoint responds.
3. Write `examples/github_dashboard/SOLUTION.md` describing what you built,
   the key files created, how to run it, and any limitations.
"""

    print("\n── Phase 1: Build ─────────────────────────────────────────────────")

    def on_tool(msg_id: str, part_index: int, state: dict) -> None:
        status = state.get("status", "")
        name   = state.get("tool_name", "tool")
        inp    = state.get("input") or {}
        arg    = next(iter(inp.values()), "") if inp else ""
        if isinstance(arg, str) and len(arg) > 120:
            arg = arg[:120] + "…"
        icons = {"running": "◎", "completed": "●", "error": "✗"}
        if icons.get(status):
            print(f"\n  {icons[status]} {name}  {arg}", flush=True)

    with OpenVibe() as ov:
        session = ov.create_session()
        response = session.send(
            prompt,
            on_token=lambda t: print(t, end="", flush=True),
            on_tool=on_tool,
        )
        while response.state == SessionState.WAITING:
            req = response.request
            print(f"\n  [perm] {req.description}  → allow")
            response = session.reply(req.id, "allow")
        if response.state == SessionState.ERROR:
            print(f"\n  error: {response.error.message}")
            return ""

    print("\n")
    if _SOLUTION_FILE.exists():
        return _SOLUTION_FILE.read_text(encoding="utf-8")
    return response.text or ""


# ── Phase 2: Evaluate ─────────────────────────────────────────────────────────

async def _run_evaluate_phase(
    solution_text: str,
    model: str,
    n: int,
    max_steps: int,
):
    from openvibe.llm import LiteLLMBackend
    from openvibe.sim import HarnessConfig, SimHarness

    task_text = _TASK_FILE.read_text(encoding="utf-8")

    # Context is just what the user asked for + what was actually built.
    # SimDesigner derives the full evaluation environment from this automatically.
    context = "\n\n".join([
        "## Task",
        task_text.strip(),
        "## What Was Built",
        solution_text.strip() or "(no solution file found)",
    ])

    print("\n── Phase 2: Evaluate ───────────────────────────────────────────────")
    print(f"  Model     : {model}")
    print(f"  Scenarios : {n}")
    print(f"  Output    : {_OUTPUT_DIR}\n")

    def on_progress(msg: str, current: int, total: int) -> None:
        bar = "█" * current + "░" * (total - current)
        print(f"  [{bar}] {msg}")

    config = HarnessConfig(
        context=context,
        model=model,
        n_generate=n,
        max_steps=max_steps,
        output_dir=str(_OUTPUT_DIR),
        dataset_name="github_dashboard",
        tags=["github", "dashboard"],
    )
    harness = SimHarness(config=config, llm=LiteLLMBackend(), on_progress=on_progress)
    report  = await harness.run()

    _save_full_report(report, harness)
    _print_report(report)
    print(f"\n  Full report saved → {_OUTPUT_DIR}/")
    return report


# ── Phase 3: Improve ─────────────────────────────────────────────────────────

def _run_improvement_phase(report) -> str:
    """Run openvibe again with the evaluation report as a guide for improvements."""
    from openvibe import OpenVibe, SessionState

    task_text = _TASK_FILE.read_text(encoding="utf-8")
    solution_text = _SOLUTION_FILE.read_text(encoding="utf-8") if _SOLUTION_FILE.exists() else ""

    # Build a concise improvement brief from the report
    failing = [r for r in report.results if r.total_score < 0.7]
    weak_criteria = sorted(report.by_criterion.items(), key=lambda x: x[1])[:3]

    brief_lines = [
        "The following areas scored lowest in evaluation:",
        "",
    ]
    for crit, score in weak_criteria:
        brief_lines.append(f"- {crit}: {score:.3f}")

    if failing:
        brief_lines += ["", "Failing scenarios:"]
        for r in sorted(failing, key=lambda x: x.total_score):
            brief_lines.append(f"- [{r.difficulty}] {r.scenario_title} ({r.total_score:.3f})")
            if r.improvement_suggestions:
                brief_lines.append(f"  Suggestion: {r.improvement_suggestions[0]}")
            brief_lines.append(f"  Feedback: {r.feedback}")

    brief = "\n".join(brief_lines)

    prompt = f"""You previously built the following:

## Original Task
{task_text}

## What You Built
{solution_text}

## Evaluation Results
Mean score: {report.mean_total_score:.3f} | Pass rate: {report.pass_rate:.1%}

{brief}

Improve the implementation to address these weaknesses. Make targeted, focused changes
to the relevant files. After improving, update `examples/github_dashboard/SOLUTION.md`
to describe what changed and why.
"""

    print("\n── Phase 3: Improve ────────────────────────────────────────────────")

    def on_tool(msg_id: str, part_index: int, state: dict) -> None:
        status = state.get("status", "")
        name   = state.get("tool_name", "tool")
        inp    = state.get("input") or {}
        arg    = next(iter(inp.values()), "") if inp else ""
        if isinstance(arg, str) and len(arg) > 120:
            arg = arg[:120] + "…"
        icons = {"running": "◎", "completed": "●", "error": "✗"}
        if icons.get(status):
            print(f"\n  {icons[status]} {name}  {arg}", flush=True)

    with OpenVibe() as ov:
        session = ov.create_session()
        response = session.send(
            prompt,
            on_token=lambda t: print(t, end="", flush=True),
            on_tool=on_tool,
        )
        while response.state == SessionState.WAITING:
            req = response.request
            print(f"\n  [perm] {req.description}  → allow")
            response = session.reply(req.id, "allow")
        if response.state == SessionState.ERROR:
            print(f"\n  error: {response.error.message}")
            return solution_text

    print("\n")
    if _SOLUTION_FILE.exists():
        return _SOLUTION_FILE.read_text(encoding="utf-8")
    return response.text or solution_text


# ── Report persistence ────────────────────────────────────────────────────────

def _save_full_report(report, harness) -> None:
    """Save a human-readable markdown report alongside the existing JSON output."""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # The harness already saves report.json and dataset.jsonl.
    # We additionally write a full markdown report.
    md_path = _OUTPUT_DIR / f"{report.dataset_name}_report.md"
    md_path.write_text(_build_markdown_report(report), encoding="utf-8")


def _build_markdown_report(report) -> str:
    lines = [
        f"# Evaluation Report: {report.dataset_name}",
        "",
        "## Simulation Context",
        "",
        f"> {report.context[:300]}{'…' if len(report.context) > 300 else ''}",
        "",
        "## Summary Metrics",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Scenarios evaluated | {report.evaluated} / {report.total_scenarios} |",
        f"| Mean score | {report.mean_total_score:.3f} |",
        f"| Median score | {report.median_total_score:.3f} |",
        f"| Pass rate (≥ 0.7) | {report.pass_rate:.1%} |",
        f"| Outcome accuracy | {report.outcome_accuracy:.1%} |",
        "",
        "## Results by Difficulty",
        "",
    ]
    order = ["easy", "medium", "hard", "adversarial"]
    for diff, score in sorted(
        report.by_difficulty.items(),
        key=lambda x: order.index(x[0]) if x[0] in order else 99,
    ):
        bar  = "█" * int(score * 20) + "░" * (20 - int(score * 20))
        flag = "✓" if score >= 0.7 else "✗"
        lines.append(f"- **{diff}**: {bar} {score:.3f} {flag}")

    lines += ["", "## Results by Criterion", ""]
    for crit, score in sorted(report.by_criterion.items(), key=lambda x: -x[1]):
        lines.append(f"- **{crit}**: {score:.3f}")

    lines += ["", "## Individual Scenario Results", ""]
    for r in sorted(report.results, key=lambda x: -x.total_score):
        icon = "✓" if r.total_score >= 0.7 else "✗"
        lines.append(f"### {icon} [{r.difficulty}] {r.scenario_title}")
        lines.append(f"**Score**: {r.total_score:.3f}  |  **Outcome match**: {'yes' if r.outcome_match else 'no'}")
        lines.append("")
        lines.append(f"**Feedback**: {r.feedback}")
        if r.criterion_scores:
            lines.append("")
            lines.append("| Criterion | Score | Weighted |")
            lines.append("|-----------|-------|---------|")
            for cs in r.criterion_scores:
                lines.append(f"| {cs.criterion} | {cs.score:.3f} | {cs.weighted_score:.3f} |")
        if r.improvement_suggestions:
            lines.append("")
            lines.append("**Improvement suggestions:**")
            for s in r.improvement_suggestions:
                lines.append(f"- {s}")
        lines.append("")

    return "\n".join(lines)


# ── Entrypoint ────────────────────────────────────────────────────────────────

async def main() -> None:
    args = parse_args()

    from openvibe.config import load_config
    load_config()

    from openvibe.llm import resolve_model
    model = args.model or resolve_model()

    print(f"\nGitHub Analytics Dashboard — build and evaluate")
    print(f"Model : {model}  |  Phase : {args.phase}")

    solution_text = ""
    report = None

    if args.phase in ("full", "build"):
        solution_text = await asyncio.to_thread(_run_build_phase)

    if args.phase in ("full", "evaluate"):
        if not solution_text and _SOLUTION_FILE.exists():
            solution_text = _SOLUTION_FILE.read_text(encoding="utf-8")

        report = await _run_evaluate_phase(
            solution_text=solution_text,
            model=model,
            n=args.n,
            max_steps=args.max_steps,
        )

    if report is not None:
        try:
            answer = input("  Improve based on evaluation results? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""

        if answer in ("y", "yes"):
            solution_text = await asyncio.to_thread(_run_improvement_phase, report)
            print("\n  Re-evaluating after improvements…")
            await _run_evaluate_phase(
                solution_text=solution_text,
                model=model,
                n=args.n,
                max_steps=args.max_steps,
            )


# ── Console summary ───────────────────────────────────────────────────────────

def _print_report(report) -> None:
    print(f"\n{'═'*60}")
    print(f"  EVALUATION REPORT: {report.dataset_name}")
    print(f"{'═'*60}")
    print(f"\n  Scenarios   {report.evaluated}/{report.total_scenarios}")
    print(f"  Mean score  {report.mean_total_score:.3f}")
    print(f"  Pass rate   {report.pass_rate:.1%}")

    if report.by_difficulty:
        print(f"\n  By difficulty:")
        order = ["easy", "medium", "hard", "adversarial"]
        for diff, score in sorted(
            report.by_difficulty.items(),
            key=lambda x: order.index(x[0]) if x[0] in order else 99,
        ):
            bar  = "█" * int(score * 20) + "░" * (20 - int(score * 20))
            flag = "✓" if score >= 0.7 else "✗"
            print(f"    {diff:<14} {bar} {score:.3f} {flag}")

    if report.by_criterion:
        print(f"\n  By criterion:")
        for crit, score in sorted(report.by_criterion.items(), key=lambda x: -x[1]):
            print(f"    {crit:<34} {score:.3f}")

    if report.results:
        failing = [r for r in report.results if r.total_score < 0.7]
        if failing:
            print(f"\n  Needs improvement:")
            for r in sorted(failing, key=lambda x: x.total_score)[:3]:
                print(f"    ✗ [{r.difficulty}] {r.scenario_title} — {r.total_score:.3f}")
                if r.improvement_suggestions:
                    print(f"      → {r.improvement_suggestions[0]}")

    print(f"\n{'═'*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
