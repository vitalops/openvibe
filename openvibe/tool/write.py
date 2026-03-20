"""Write tool — create or overwrite a file with given content."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from openvibe.tool.base import Tool, ToolContext, ToolResult


class WriteTool(Tool):
    name = "write"
    description = (
        "Write content to a file, creating it (and any parent directories) "
        "if it does not exist, or overwriting it if it does. "
        "Prefer ``edit`` for targeted modifications to existing files."
    )

    class Params(Tool.Params):
        path: str = Field(description="Absolute or project-relative path to write.")
        content: str = Field(description="Full file content to write.")

    async def execute(self, ctx: ToolContext, params: "WriteTool.Params") -> ToolResult:
        path = _resolve(params.path, ctx.working_dir)

        await ctx.check_permission(
            tool="write",
            argument=str(path),
            description=f"Write {path}",
        )

        is_new = not path.exists()

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params.content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(title=f"Write {params.path}", output=str(exc), error=True)

        action = "Created" if is_new else "Updated"
        lines = params.content.count("\n") + 1
        return ToolResult(
            title=f"{action} {params.path}",
            output=f"{action} {path} ({lines} lines)",
        )


def _resolve(path_str: str, working_dir: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else Path(working_dir) / p
