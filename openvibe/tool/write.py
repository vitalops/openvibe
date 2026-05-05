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
        if path is None:
            return ToolResult(
                title=f"Write {params.path}",
                output=(
                    f"'{params.path}' is a bare filename with no directory component. "
                    "Provide an absolute path so the file is written to the correct location. "
                    "Use a screenshot or the current environment to determine the right directory."
                ),
                error=True,
            )

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


def _resolve(path_str: str, working_dir: str) -> Path | None:
    p = Path(path_str)
    if p.is_absolute():
        return p
    # Explicit project-relative: starts with ./ or ../ or has a directory component
    if path_str.startswith(("./", "../")) or p.parent != Path("."):
        return Path(working_dir) / p
    # Bare filename (e.g. "output.docx") — no implicit directory; caller must provide absolute path
    return None
