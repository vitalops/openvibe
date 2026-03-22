"""Read tool — read file contents with optional line range."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from openvibe.tool.base import MAX_OUTPUT_CHARS, Tool, ToolContext, ToolResult


class ReadTool(Tool):
    name = "read"
    description = (
        "Read the contents of a file. Optionally specify a line range to "
        "read a subset of a large file. Returns line numbers alongside "
        "content so the model can reference them in edits."
    )

    class Params(Tool.Params):
        path: str = Field(description="Absolute or project-relative file path.")
        start_line: int | None = Field(
            default=None, ge=1, description="First line to read (1-indexed, inclusive)."
        )
        end_line: int | None = Field(
            default=None, ge=1, description="Last line to read (1-indexed, inclusive)."
        )

    async def execute(self, ctx: ToolContext, params: "ReadTool.Params") -> ToolResult:
        path = _resolve(params.path, ctx.working_dir)

        await ctx.check_permission(
            tool="read",
            argument=str(path),
            description=f"Read {path}",
        )

        if not path.exists():
            return ToolResult(
                title=f"Read {params.path}",
                output=f"File not found: {path}",
                error=True,
            )

        if not path.is_file():
            return ToolResult(
                title=f"Read {params.path}", output=f"Not a file: {path}", error=True
            )

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return ToolResult(title=f"Read {params.path}", output=str(exc), error=True)

        start = (params.start_line or 1) - 1
        end = params.end_line or len(lines)
        selected = lines[start:end]

        # Format with line numbers (matches the Read tool convention in the TS version)
        numbered = "\n".join(
            f"{start + i + 1:>6}\t{line}" for i, line in enumerate(selected)
        )

        return ToolResult(
            title=f"Read {params.path}",
            output=numbered,
            metadata={"total_lines": len(lines), "shown_lines": len(selected)},
        )


def _resolve(path_str: str, working_dir: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    return Path(working_dir) / p
