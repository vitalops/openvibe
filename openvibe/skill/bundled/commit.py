"""Built-in /commit skill (alias /gc).

Generates a well-structured git commit for staged or recent changes.
"""

from __future__ import annotations

from openvibe.skill.base import SkillDefinition


class CommitSkill(SkillDefinition):
    name = "commit"
    description = "Generate and create a git commit for the current changes."
    aliases: list[str] = ["gc"]
    user_invocable = True
    when_to_use = "When you want to commit work-in-progress or completed changes."
    argument_hint = "[commit message hint]"

    def get_prompt(self, args: str) -> str:
        hint = f"\n\nHint from user: {args.strip()}" if args.strip() else ""
        return f"""\
Create a git commit for the current changes.{hint}

Steps:

1. Run `git status` to see which files are modified, staged, and untracked.

2. Run `git diff` (and `git diff --cached` if anything is staged) to review
   the actual changes.

3. Run `git log --oneline -10` to understand this repository's commit message
   style and tense.

4. Draft a commit message:
   - First line: ≤72 characters, imperative mood ("Add X", "Fix Y", "Remove Z").
   - If the change needs more explanation, add a blank line then a short
     paragraph (or bullet list) describing *why*, not just *what*.
   - Do NOT mention file names in the subject unless they are the entire point.

5. Stage all relevant files (`git add <files>`) — be explicit; avoid `git add -A`
   unless you have confirmed there are no secrets or generated files.

6. Commit with the drafted message.

7. Confirm with `git log -1 --stat` and show the result.
"""
