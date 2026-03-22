"""Edit tool — targeted string replacement in existing files.

The model provides an ``old_string`` that must appear exactly once in the file
and a ``new_string`` to replace it with.  Multiple replacements can be batched
via the ``edits`` list for efficiency.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from openvibe.tool.base import Tool, ToolContext, ToolResult


class _Edit(BaseModel):
    old_string: str = Field(
        description="Exact string to find. Must appear exactly once."
    )
    new_string: str = Field(description="Replacement string.")


class EditTool(Tool):
    name = "edit"
    description = (
        "Replace exact substrings in an existing file. Each edit must "
        "uniquely identify a location — if ``old_string`` appears more than "
        "once the edit is rejected. Use ``write`` to replace whole files."
    )

    class Params(Tool.Params):
        path: str = Field(description="Absolute or project-relative file path.")
        edits: list[_Edit] = Field(
            min_length=1,
            description="One or more old→new string replacements, applied in order.",
        )

    async def execute(self, ctx: ToolContext, params: "EditTool.Params") -> ToolResult:
        path = _resolve(params.path, ctx.working_dir)

        await ctx.check_permission(
            tool="edit",
            argument=str(path),
            description=f"Edit {path}",
        )

        if not path.exists():
            return ToolResult(
                title=f"Edit {params.path}",
                output=f"File not found: {path}",
                error=True,
            )

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult(title=f"Edit {params.path}", output=str(exc), error=True)

        original = content
        errors: list[str] = []

        for edit in params.edits:
            count = content.count(edit.old_string)
            if count == 0:
                errors.append(f"String not found: {edit.old_string!r}")
            elif count > 1:
                errors.append(
                    f"String appears {count} times (must be unique): {edit.old_string!r}"
                )
            else:
                content = content.replace(edit.old_string, edit.new_string, 1)

        if errors:
            return ToolResult(
                title=f"Edit {params.path}",
                output="\n".join(errors),
                error=True,
            )

        if content == original:
            return ToolResult(
                title=f"Edit {params.path}",
                output="No changes made (old and new strings are identical).",
            )

        try:
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(title=f"Edit {params.path}", output=str(exc), error=True)

        edits_applied = len(params.edits)
        return ToolResult(
            title=f"Edited {params.path}",
            output=f"Applied {edits_applied} edit(s) to {path}.",
        )


def _resolve(path_str: str, working_dir: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else Path(working_dir) / p
