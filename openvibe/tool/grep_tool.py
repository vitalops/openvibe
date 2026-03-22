"""Grep tool — search file contents with regex or literal patterns."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pydantic import Field

from openvibe.tool.base import Tool, ToolContext, ToolResult


class GrepTool(Tool):
    name = "grep"
    description = (
        "Search file contents for a pattern (regex or literal). "
        "Returns matching lines with file path and line number. "
        "Delegates to ``ripgrep`` (rg) when available, falls back to "
        "Python's ``re`` module."
    )

    class Params(Tool.Params):
        pattern: str = Field(description="Regex or literal search pattern.")
        path: str | None = Field(
            default=None,
            description="File or directory to search. Defaults to project root.",
        )
        glob: str | None = Field(
            default=None,
            description="Restrict search to files matching this glob (e.g. '*.py').",
        )
        case_insensitive: bool = Field(
            default=False, description="Case-insensitive search."
        )
        fixed_strings: bool = Field(
            default=False, description="Treat pattern as a literal string, not a regex."
        )

    async def execute(self, ctx: ToolContext, params: "GrepTool.Params") -> ToolResult:
        search_path = params.path or ctx.working_dir
        title = f"Grep '{params.pattern}' in {search_path}"

        # Try ripgrep first
        rg_result = _try_ripgrep(params, search_path)
        if rg_result is not None:
            lines, error = rg_result
        else:
            lines, error = _python_grep(params, search_path)

        if error:
            return ToolResult(title=title, output=error, error=True)

        if not lines:
            return ToolResult(title=title, output="No matches found.")

        return ToolResult(
            title=f"{title} ({len(lines)} matches)",
            output="\n".join(lines[:500]),
            metadata={"match_count": len(lines)},
        )


def _try_ripgrep(
    params: "GrepTool.Params", search_path: str
) -> tuple[list[str], str] | None:
    try:
        cmd = ["rg", "--line-number", "--no-heading", "--color=never"]
        if params.case_insensitive:
            cmd.append("-i")
        if params.fixed_strings:
            cmd.append("-F")
        if params.glob:
            cmd += ["-g", params.glob]
        cmd += [params.pattern, search_path]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout.splitlines(), (
            result.stderr.strip() if result.returncode > 1 else ""
        )
    except FileNotFoundError:
        return None  # rg not installed
    except subprocess.TimeoutExpired:
        return [], "Search timed out."


def _python_grep(params: "GrepTool.Params", search_path: str) -> tuple[list[str], str]:
    base = Path(search_path)
    flags = re.IGNORECASE if params.case_insensitive else 0

    try:
        if params.fixed_strings:
            needle = re.escape(params.pattern)
        else:
            needle = params.pattern
        compiled = re.compile(needle, flags)
    except re.error as exc:
        return [], f"Invalid regex: {exc}"

    import fnmatch as _fnmatch

    matches: list[str] = []
    paths = [base] if base.is_file() else base.rglob("*")

    for path in paths:
        if not path.is_file():
            continue
        if params.glob and not _fnmatch.fnmatch(path.name, params.glob):
            continue
        try:
            for i, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if compiled.search(line):
                    matches.append(f"{path}:{i}:{line}")
                    if len(matches) >= 500:
                        return matches, ""
        except OSError:
            continue

    return matches, ""
