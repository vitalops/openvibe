"""AppTool — open, close, focus, and list running applications.

Platform support
----------------
macOS   — uses ``open -a`` / AppleScript via ``osascript``
Linux   — uses ``xdg-open`` / ``wmctrl`` / ``xdotool``
Windows — uses ``start`` shell command / ``pygetwindow``

All actions are gated by the session sandbox allow-list.
"""

from __future__ import annotations

import asyncio
import platform
import subprocess
import sys
from typing import Literal

from pydantic import Field

from openvibe.tool.base import Tool, ToolContext, ToolResult

_PLATFORM = platform.system()  # "Darwin", "Linux", "Windows"


class AppTool(Tool):
    """Open, close, focus, or list desktop applications."""

    name = "app"
    description = (
        "Interact with desktop applications: open an app by name, close it, "
        "bring it to the foreground, or list all currently running windows. "
        "Useful for launching IDEs, browsers, terminals, and other tools."
    )

    class Params(Tool.Params):
        action: Literal["open", "close", "focus", "list"] = Field(
            description=(
                "Application action:\n"
                "  open  — launch an application by name or path\n"
                "  close — quit a running application by name\n"
                "  focus — bring a window to the foreground by name\n"
                "  list  — list currently open windows / running applications"
            )
        )
        name: str | None = Field(
            default=None,
            description=(
                "Application name (e.g. 'Terminal', 'Google Chrome', 'VS Code') "
                "or full path to executable. Required for open/close/focus."
            ),
        )

    async def execute(self, ctx: ToolContext, params: "AppTool.Params") -> ToolResult:  # type: ignore[override]
        from openvibe.computer.sandbox import ActionType, get_sandbox

        app_arg = params.name or "(list)"
        await ctx.check_permission(
            tool="app",
            argument=f"{params.action} {app_arg}",
            description=f"App control: {params.action} '{app_arg}'",
        )

        sandbox = get_sandbox(ctx.session_id)

        # Enforce allow-list for mutating actions
        if params.action in ("open", "close", "focus") and params.name:
            if not sandbox.is_app_allowed(params.name):
                return ToolResult(
                    title="App action denied",
                    output=(
                        f"Application '{params.name}' is not in the allow-list for this session. "
                        f"Allowed: {sandbox.allowed_apps or ['(all)']}"
                    ),
                    error=True,
                )

        action_map = {
            "open": ActionType.APP_OPEN,
            "close": ActionType.APP_CLOSE,
            "focus": ActionType.APP_FOCUS,
            "list": ActionType.APP_LIST,
        }

        try:
            loop = asyncio.get_event_loop()
            result_msg = await loop.run_in_executor(None, self._do_action, params)
        except Exception as exc:
            await sandbox.record_action(
                action_map.get(params.action, ActionType.APP_OPEN),
                params={"action": params.action, "name": params.name},
                error=str(exc),
            )
            return ToolResult(
                title="App error",
                output=f"App action '{params.action}' failed: {exc}",
                error=True,
            )

        await sandbox.record_action(
            action_map.get(params.action, ActionType.APP_OPEN),
            params={"action": params.action, "name": params.name},
            result=result_msg[:200],
        )

        return ToolResult(
            title=f"App: {params.action} '{params.name or ''}'",
            output=result_msg,
        )

    # ------------------------------------------------------------------
    # Implementation — dispatches to platform-specific helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _do_action(params: "AppTool.Params") -> str:
        if params.action == "list":
            return _list_windows()

        if not params.name:
            raise ValueError(f"name is required for action='{params.action}'.")

        if params.action == "open":
            return _open_app(params.name)
        if params.action == "close":
            return _close_app(params.name)
        if params.action == "focus":
            return _focus_app(params.name)

        raise ValueError(f"Unknown app action: {params.action!r}")


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=15,
        **kwargs,
    )


# ---- open ------------------------------------------------------------------

def _open_app(name: str) -> str:
    import time

    if _PLATFORM == "Darwin":
        # Launch the app first so it is running before AppleScript activates it.
        r = _run(["open", "-a", name])
        if r.returncode != 0:
            r2 = _run(["open", name])
            if r2.returncode != 0:
                raise RuntimeError(r.stderr.strip() or r2.stderr.strip())
        # Wait for the process to start before AppleScript can address it.
        time.sleep(1.5)

        # Activate and create a new document if the app is document-based.
        # The `try` block is intentional: `make new document` fails silently
        # for apps that don't support it (browsers, media players, etc.).
        script = (
            f'tell application "{name}"\n'
            f'    activate\n'
            f'    try\n'
            f'        if (count of documents) = 0 then make new document\n'
            f'    end try\n'
            f'end tell'
        )
        _run(["osascript", "-e", script])
        time.sleep(0.5)  # let window settle after document creation
        return f"Opened '{name}' on macOS."

    if _PLATFORM == "Linux":
        # Try launching as a command first, then xdg-open as fallback
        try:
            subprocess.Popen([name], start_new_session=True)
        except FileNotFoundError:
            subprocess.Popen(["xdg-open", name], start_new_session=True)
        time.sleep(2.0)
        return f"Opened '{name}' on Linux."

    if _PLATFORM == "Windows":
        subprocess.Popen(["start", "", name], shell=True, start_new_session=True)
        time.sleep(2.0)
        return f"Opened '{name}' on Windows."

    raise RuntimeError(f"Unsupported platform: {_PLATFORM}")


# ---- close -----------------------------------------------------------------

def _close_app(name: str) -> str:
    if _PLATFORM == "Darwin":
        script = f'tell application "{name}" to quit'
        r = _run(["osascript", "-e", script])
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        return f"Quit '{name}' via AppleScript."

    if _PLATFORM == "Linux":
        r = _run(["pkill", "-f", name])
        if r.returncode not in (0, 1):
            raise RuntimeError(r.stderr.strip())
        return f"Sent SIGTERM to processes matching '{name}'."

    if _PLATFORM == "Windows":
        r = _run(["taskkill", "/IM", name, "/F"])
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        return f"Terminated '{name}' on Windows."

    raise RuntimeError(f"Unsupported platform: {_PLATFORM}")


# ---- focus -----------------------------------------------------------------

def _focus_app(name: str) -> str:
    if _PLATFORM == "Darwin":
        script = f'tell application "{name}" to activate'
        r = _run(["osascript", "-e", script])
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        return f"Focused '{name}' via AppleScript."

    if _PLATFORM == "Linux":
        # wmctrl is a common tool for X11 window management
        r = _run(["wmctrl", "-a", name])
        if r.returncode != 0:
            # Try xdotool as fallback
            r2 = _run(["xdotool", "search", "--name", name, "windowactivate"])
            if r2.returncode != 0:
                raise RuntimeError(
                    f"wmctrl: {r.stderr.strip()} | xdotool: {r2.stderr.strip()}"
                )
        return f"Focused window matching '{name}'."

    if _PLATFORM == "Windows":
        try:
            import pygetwindow as gw  # type: ignore[import-not-found]

            wins = gw.getWindowsWithTitle(name)
            if not wins:
                raise RuntimeError(f"No window found with title '{name}'.")
            wins[0].activate()
            return f"Focused '{wins[0].title}'."
        except ImportError as exc:
            raise RuntimeError(
                "pygetwindow is required on Windows: pip install pygetwindow"
            ) from exc

    raise RuntimeError(f"Unsupported platform: {_PLATFORM}")


# ---- list ------------------------------------------------------------------

def _list_windows() -> str:
    if _PLATFORM == "Darwin":
        script = (
            'tell application "System Events" to get the name of every process '
            'whose background only is false'
        )
        r = _run(["osascript", "-e", script])
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        names = [n.strip() for n in r.stdout.strip().split(",") if n.strip()]
        return "Running applications:\n" + "\n".join(f"  • {n}" for n in names)

    if _PLATFORM == "Linux":
        # Try wmctrl first (X11), then fallback to /proc
        r = _run(["wmctrl", "-l"])
        if r.returncode == 0:
            lines = [ln.strip() for ln in r.stdout.strip().splitlines() if ln.strip()]
            return f"Open windows ({len(lines)}):\n" + "\n".join(
                f"  • {ln}" for ln in lines
            )
        # Fallback: list process names
        r2 = _run(["ps", "-eo", "comm="])
        if r2.returncode == 0:
            procs = sorted(set(r2.stdout.strip().splitlines()))
            return "Running processes:\n" + "\n".join(f"  • {p}" for p in procs[:50])
        raise RuntimeError("Could not list windows: wmctrl and ps both failed.")

    if _PLATFORM == "Windows":
        try:
            import pygetwindow as gw  # type: ignore[import-not-found]

            titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
            return f"Open windows ({len(titles)}):\n" + "\n".join(
                f"  • {t}" for t in titles
            )
        except ImportError:
            # Fallback to tasklist
            r = _run(["tasklist", "/FO", "CSV", "/NH"])
            if r.returncode == 0:
                lines = r.stdout.strip().splitlines()[:30]
                return "Running processes:\n" + "\n".join(f"  • {l}" for l in lines)
            raise RuntimeError("Could not list windows on Windows.")

    raise RuntimeError(f"Unsupported platform: {_PLATFORM}")
