"""Built-in /simplify skill.

Reviews recently changed code for reuse opportunities, quality issues,
and efficiency improvements, then fixes what it finds.
"""

from __future__ import annotations

from openvibe.skill.base import SkillDefinition


class SimplifySkill(SkillDefinition):
    name = "simplify"
    description = "Review changed code for reuse, quality, and efficiency, then fix any issues found."
    aliases: list[str] = []
    user_invocable = True
    when_to_use = "After writing or editing code to catch and fix common issues."
    argument_hint = "[focus area]"

    def get_prompt(self, args: str) -> str:
        focus = f"\n\nFocus area: {args}" if args.strip() else ""
        return f"""\
Review the code changes in this session for quality issues, then fix what you find.{focus}

Follow these steps:

1. Run `git diff HEAD` (or inspect recently written/edited files) to identify all changes.

2. Launch three parallel review passes:
   - **Reuse review**: Look for duplicated logic that could be extracted into a helper,
     repeated patterns that belong in a shared utility, or code that already exists
     elsewhere in the repo.
   - **Quality review**: Check naming clarity, error handling, edge cases, type hints,
     missing tests, and anything that would confuse a reviewer.
   - **Efficiency review**: Identify unnecessary loops, redundant I/O, avoidable
     re-computation, or data structures better suited to the access pattern.

3. Aggregate findings. For each issue, decide:
   - Is it a real problem worth fixing right now? (Skip cosmetic nits.)
   - What is the minimal change that resolves it without over-engineering?

4. Apply fixes directly. Do not ask for confirmation unless a change is destructive
   or would affect files outside the current diff.

5. When done, summarise what was changed and why in one short paragraph.
"""
