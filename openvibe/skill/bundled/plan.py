"""Built-in /plan skill.

Produces a concrete implementation plan for a feature, refactor, or task.
"""

from __future__ import annotations

from openvibe.skill.base import SkillDefinition


class PlanSkill(SkillDefinition):
    name = "plan"
    description = "Produce a concrete, step-by-step implementation plan."
    aliases: list[str] = []
    user_invocable = True
    when_to_use = "Before starting a non-trivial task to align on approach and surface risks early."
    argument_hint = "<task description>"

    def get_prompt(self, args: str) -> str:
        task = args.strip() or "the task described in this session"
        return f"""\
Create a concrete implementation plan for: {task}

Steps:

1. **Understand the scope** — read the relevant source files, tests, and
   configuration. Use glob/grep to explore if you are unsure where things live.

2. **Identify constraints** — existing patterns, frameworks in use, permission
   boundaries, performance requirements, and anything that limits options.

3. **Draft the plan** — produce a numbered list of discrete, ordered steps.
   Each step must be:
   - Actionable (a specific file change, command, or decision)
   - Atomic (one logical change per step)
   - Justified (a brief reason why)

4. **Call out risks** — list 2-4 things that could go wrong and how to mitigate
   them (e.g. breaking changes, race conditions, missing tests).

5. **Estimate complexity** — S / M / L and a one-sentence explanation.

Do NOT start implementing yet. Return only the plan.
"""
