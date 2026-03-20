"""Glob tool — find files matching a pattern."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from pydantic import Field

from openvibe.tool.base import Tool, ToolContext, ToolResult


class GlobTool(Tool):
    name = "glob"
    description = (
        "Find files whose paths match a glob pattern (e.g. ``**/*.py``, "
        "``src/**/*.ts``). Results are sorted by modification time (newest "
        "first) and capped at 200 entries."
    )

    class Params(Tool.Params):
        pattern: str = Field(description="Glob pattern, e.g. '**/*.py'.")
        path: str | None = Field(
            default=None,
            description="Directory to search in. Defaults to the project root.",
        )

    async def execute(self, ctx: ToolContext, params: "GlobTool.Params") -> ToolResult:
        base = Path(params.path or ctx.working_dir)
        if not base.is_absolute():
            base = Path(ctx.working_dir) / base

        try:
            matches = sorted(
                base.rglob(params.pattern),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError as exc:
            return ToolResult(title=f"Glob {params.pattern}", output=str(exc), error=True)

        # Only return files, not directories; cap at 200
        files = [str(p) for p in matches if p.is_file()][:200]

        if not files:
            return ToolResult(
                title=f"Glob {params.pattern}",
                output="No files matched.",
            )

        return ToolResult(
            title=f"Glob {params.pattern} ({len(files)} files)",
            output="\n".join(files),
            metadata={"count": len(files)},
        )
