"""``/build-eval`` — run a two-phase build-then-evaluate pipeline from a task description."""

from __future__ import annotations

import re

from openvibe.skill.base import CostTier, SkillDefinition, SkillExample

_MD_PATH_RE = re.compile(r"[\w./\-]+\.md", re.IGNORECASE)


class BuildAndEvalSkill(SkillDefinition):
    name = "build-eval"
    description = (
        "Run a two-phase pipeline: Phase 1 builds something real from a task "
        "description using tools; Phase 2 runs the simulation harness to evaluate "
        "the produced artifact automatically."
    )
    aliases = ["build_eval", "beval"]
    capabilities = ["build", "evaluation", "simulation", "agentic-loop", "app-building"]
    input_types = ["file_path", "task_description"]
    output_types = ["evaluation_report", "artifact", "solution_summary"]
    tags = ["build", "evaluate", "agentic", "harness", "pipeline"]
    cost_estimate = CostTier.HIGH
    user_invocable = True
    when_to_use = (
        "When the user gives a task to build something (an app, API, tool, script). "
        "Phase 1 builds it using available tools. Phase 2 automatically evaluates "
        "the outcome through the simulation harness — no manual evaluation setup needed."
    )
    argument_hint = "[path/to/TASK.md or description] — defaults to TASK.md in the current directory"
    examples = [
        SkillExample(
            "examples/github_dashboard/TASK.md",
            "Build a GitHub analytics dashboard then evaluate it via the simulation harness",
        ),
        SkillExample(
            "",
            "No path given — looks for TASK.md in the current directory",
        ),
    ]

    def match_intent(self, text: str) -> float:
        lower = text.lower()
        score = 0.0

        has_task_md = "task.md" in lower
        has_md_path = bool(_MD_PATH_RE.search(text))
        has_build   = "build" in lower
        has_eval    = any(w in lower for w in ("eval", "evaluate", "test", "check", "score"))
        has_run     = any(w in lower for w in ("run", "execute", "start"))

        if has_task_md and (has_build or has_eval or has_run):
            score += 0.7
        elif has_task_md:
            score += 0.2

        if has_md_path and has_build and has_eval:
            score += 0.4
        elif has_md_path and (has_build or has_eval):
            score += 0.2

        if has_build and has_eval:
            score += 0.3

        if "pipeline" in lower or "harness" in lower:
            score += 0.2

        return min(score, 1.0)

    def extract_args(self, text: str) -> str:
        match = _MD_PATH_RE.search(text)
        return match.group(0) if match else ""

    def get_prompt(self, args: str) -> str:
        task_file = args.strip() or "TASK.md"
        # Derive where to save the report (sibling directory to the task file)
        import os
        task_dir = os.path.dirname(task_file) or "."
        output_path = os.path.join(task_dir, "eval_output")

        return f"""Run a two-phase build-and-evaluate pipeline.

## Phase 1 — Build

1. Read `{task_file}` to understand what needs to be built.
2. Use your available tools (read_file, write_file, bash, search, etc.) to build it
   completely from scratch, following the task description exactly.
3. Verify your work: run what you built, check it responds or functions correctly.
4. Write a `SOLUTION.md` file in the same directory as `{task_file}` containing:
   - What you built (one paragraph)
   - Key files created and what each one does
   - How to run or use what you built
   - Any limitations or known issues

Do **not** proceed to Phase 2 until Phase 1 is fully complete and verified.

## Phase 2 — Evaluate

5. Read `{task_file}` (the original task) and `SOLUTION.md` (what was built).
6. Call the `simulate` tool with:
   - `context` = the full text of `{task_file}` followed by the full text of `SOLUTION.md`
   - `n_scenarios` = 5
   - `mode` = "full"
   - `output_path` = "{output_path}"
   - `working_dir` = "{task_dir}"

   The simulation harness will automatically:
   - Design a complete evaluation environment (personas, tools, criteria) suited to what was built
   - Generate shell commands to execute the real artifact during simulation
   - Run each scenario by actually invoking the built artifact and scoring the real outputs
   - Save a full report to `{output_path}/`

7. Present the evaluation summary:
   - Overall score and pass rate
   - Breakdown by difficulty and criterion
   - The weakest area and one concrete improvement suggestion

The harness evaluates your **actual output**. It designs its own evaluation criteria
from what you built — you do not need to specify them.

## Phase 3 — Improve (optional)

8. After presenting the evaluation summary, ask the user exactly this:

   "Would you like me to use the evaluation results to improve the implementation?"

9. If the user says yes:
   - Read the evaluation report from `{output_path}/`
   - Identify the failing scenarios and lowest-scoring criteria
   - Make targeted improvements to the built artifact, guided by the feedback
     and improvement suggestions in the report
   - Update `SOLUTION.md` to reflect what changed
   - Re-run the `simulate` tool with the same context and `output_path` to
     produce an updated report showing the improvement

   If the user says no, stop here."""
