"""Built-in /debug skill.

Systematically diagnoses and fixes a bug or error in the codebase.
"""

from __future__ import annotations

from openvibe.skill.base import SkillDefinition


class DebugSkill(SkillDefinition):
    name = "debug"
    description = "Systematically diagnose and fix a bug or error."
    aliases: list[str] = []
    user_invocable = True
    when_to_use = (
        "When you have an error, failing test, or unexpected behaviour to investigate."
    )
    argument_hint = "[error message or description]"

    def get_prompt(self, args: str) -> str:
        problem = (
            f"Problem to debug: {args.strip()}"
            if args.strip()
            else (
                "Identify the most recent error or failing behaviour in this session."
            )
        )
        return f"""\
{problem}

Debug this systematically:

1. **Reproduce** — confirm the error is real and understand the exact conditions
   that trigger it. Run the failing test or command if possible.

2. **Locate** — read the relevant source files and stack traces. Identify the
   exact line(s) and the immediate cause (wrong value, missing check, off-by-one,
   incorrect assumption, etc.).

3. **Understand root cause** — trace back one level further. Why does the
   immediate cause exist? (Missing guard? Wrong data flow? API misuse?)

4. **Fix** — apply the minimal correct change. Do not refactor surrounding code
   unless it is directly causing the bug.

5. **Verify** — re-run the failing test or reproduce step to confirm the fix
   works. Check that no related tests broke.

6. **Summarise** — one short paragraph: what the bug was, why it happened, and
   what was changed.
"""
