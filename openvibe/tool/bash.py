"""Bash tool — execute shell commands in the project working directory."""

from __future__ import annotations

import asyncio
import os
import signal

from pydantic import Field

from openvibe.tool.base import Tool, ToolContext, ToolResult


class BashTool(Tool):
    name = "bash"
    description = (
        "Execute a bash command in the project's working directory. "
        "Use for running tests, build commands, git operations, and any "
        "shell task. Prefer short-lived commands; long-running processes "
        "should be backgrounded explicitly."
    )

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None

    class Params(Tool.Params):
        command: str = Field(description="The bash command to run.")
        timeout: int = Field(
            default=120,
            ge=1,
            le=600,
            description="Timeout in seconds (1–600). Default: 120.",
        )
        description: str = Field(
            default="",
            description="Short human-readable description of what this command does.",
        )

    async def execute(self, ctx: ToolContext, params: "BashTool.Params") -> ToolResult:
        await ctx.check_permission(
            tool="bash",
            argument=params.command,
            description=params.description or params.command,
        )

        if ctx.abort.is_set():
            return ToolResult(
                title="Aborted",
                output="Command cancelled before execution.",
                error=True,
            )

        try:
            proc = await asyncio.create_subprocess_shell(
                params.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=ctx.working_dir,
                preexec_fn=os.setsid,
            )
            self._proc = proc
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=params.timeout,
            )
        except asyncio.TimeoutError:
            self._kill_proc()
            return ToolResult(
                title=f"$ {params.command}",
                output=f"Command timed out after {params.timeout}s.",
                error=True,
            )
        except Exception as exc:
            return ToolResult(title=f"$ {params.command}", output=str(exc), error=True)
        finally:
            self._proc = None

        combined = ""
        if stdout:
            combined += stdout.decode(errors="replace")
        if stderr:
            combined += stderr.decode(errors="replace")

        return ToolResult(
            title=f"$ {params.command}",
            output=combined.strip() or "(no output)",
            error=proc.returncode != 0,
        )

    def cancel(self) -> None:
        """Kill the running subprocess process group."""
        self._kill_proc()

    def _kill_proc(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
