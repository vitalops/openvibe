"""KeyboardTool — type text and press key combinations.

Supports three modes:
- ``type``    — type a string with simulated keystrokes
- ``press``   — press a single named key (e.g. "enter", "escape", "tab")
- ``hotkey``  — send a key combination (e.g. ["ctrl", "c"])

Key names follow pyautogui conventions (lowercase):
    enter, escape, tab, backspace, delete, up, down, left, right,
    home, end, pageup, pagedown, f1–f12, ctrl, alt, shift, cmd/win, …
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import Field

from openvibe.tool.base import Tool, ToolContext, ToolResult


def _pyautogui():  # type: ignore[return]
    try:
        import pyautogui  # type: ignore[import-not-found]
        return pyautogui
    except ImportError as exc:
        raise ImportError(
            "pyautogui is required for computer use: pip install pyautogui"
        ) from exc


def _check_accessibility() -> None:
    """Same check as in computer_mouse — raises RuntimeError if Accessibility denied."""
    import platform
    if platform.system() != "Darwin":
        return
    import subprocess
    r = subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to get name of first process'],
        capture_output=True, text=True, timeout=5,
    )
    if r.returncode != 0 and "not allowed" in (r.stderr + r.stdout).lower():
        raise RuntimeError(
            "macOS Accessibility permission is required for keyboard control. "
            "Go to System Settings → Privacy & Security → Accessibility and add your "
            "terminal application (e.g. iTerm, Terminal, VS Code)."
        )


def _type_text(pag: object, text: str, interval: float) -> None:
    """Type text robustly — uses clipboard paste for Unicode on all platforms."""
    import platform
    import time
    sys_name = platform.system()

    if sys_name == "Darwin":
        # pbcopy + cmd+v — handles full Unicode including CJK, emoji
        import subprocess
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        time.sleep(0.1)
        import pyautogui  # type: ignore[import-not-found]
        pyautogui.hotkey("command", "v")

    elif sys_name == "Linux":
        # Try xclip first, then xsel, then fall back to xdotool type
        import subprocess
        pasted = False
        for clip_cmd, paste_keys in [
            (["xclip", "-selection", "clipboard"], ["ctrl", "v"]),
            (["xsel", "--clipboard", "--input"], ["ctrl", "v"]),
        ]:
            try:
                subprocess.run(clip_cmd, input=text.encode("utf-8"), check=True,
                               capture_output=True, timeout=5)
                time.sleep(0.05)
                import pyautogui  # type: ignore[import-not-found]
                pyautogui.hotkey(*paste_keys)
                pasted = True
                break
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue

        if not pasted:
            # xdotool type as last resort (handles most Unicode via XSendEvent)
            try:
                subprocess.run(
                    ["xdotool", "type", "--clearmodifiers", "--delay",
                     str(int(interval * 1000)), "--", text],
                    check=True, timeout=30,
                )
            except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                raise RuntimeError(
                    "Cannot type text on Linux: install xclip (recommended) or xdotool. "
                    "  sudo apt install xclip   # Debian/Ubuntu\n"
                    "  sudo dnf install xclip   # Fedora"
                ) from exc

    elif sys_name == "Windows":
        # Use pyperclip for clipboard + Ctrl+V (auto-installed if absent)
        from openvibe.computer.deps import ensure_import
        pyperclip = ensure_import("pyperclip")
        pyperclip.copy(text)
        time.sleep(0.05)
        import pyautogui  # type: ignore[import-not-found]
        pyautogui.hotkey("ctrl", "v")

    else:
        # Unknown platform — best-effort ASCII via pyautogui
        import pyautogui  # type: ignore[import-not-found]
        pyautogui.typewrite(text, interval=interval)


class KeyboardTool(Tool):
    """Type text or press keyboard keys and shortcuts."""

    name = "keyboard"
    description = (
        "Simulate keyboard input: type a string of text, press a single key, "
        "or send a key combination (hotkey). Use after clicking into a text "
        "field or interactive element to enter input."
    )

    class Params(Tool.Params):
        action: Literal["type", "press", "hotkey"] = Field(
            description=(
                "Keyboard action:\n"
                "  type   — type a string of text (use for entering text into fields)\n"
                "  press  — press a single named key, e.g. 'enter', 'escape', 'tab'\n"
                "  hotkey — send a key combination, e.g. keys=['ctrl','c'] for copy"
            )
        )
        text: str | None = Field(
            default=None,
            description="Text to type. Required for action='type'.",
        )
        key: str | None = Field(
            default=None,
            description="Key name to press. Required for action='press'. E.g. 'enter', 'escape', 'tab', 'f5'.",
        )
        keys: list[str] | None = Field(
            default=None,
            description=(
                "Key names for a hotkey combination. Required for action='hotkey'. "
                "E.g. ['ctrl', 'c'] to copy, ['ctrl', 'shift', 'i'] to open DevTools."
            ),
        )
        interval: float = Field(
            default=0.02,
            description="Seconds between keystrokes when typing (action='type').",
        )
        settle_ms: int = Field(
            default=300,
            description=(
                "Milliseconds to wait after the action for the UI to settle before returning. "
                "Increase for slow apps. Default 300."
            ),
        )

    async def execute(self, ctx: ToolContext, params: "KeyboardTool.Params") -> ToolResult:  # type: ignore[override]
        from openvibe.computer.sandbox import ActionType, get_sandbox

        # Build a human-readable argument for the permission check
        if params.action == "type":
            arg_desc = f"type text: {(params.text or '')[:60]!r}"
        elif params.action == "press":
            arg_desc = f"press key: {params.key}"
        else:
            arg_desc = f"hotkey: {'+'.join(params.keys or [])}"

        await ctx.check_permission(
            tool="keyboard",
            argument=arg_desc,
            description=f"Keyboard action — {arg_desc}",
        )

        sandbox = get_sandbox(ctx.session_id)
        action_params: dict = {"action": params.action}

        try:
            loop = asyncio.get_event_loop()
            result_msg = await loop.run_in_executor(None, self._do_action, params)
        except ImportError as exc:
            return ToolResult(title="Keyboard error", output=str(exc), error=True)
        except ValueError as exc:
            return ToolResult(title="Keyboard error", output=str(exc), error=True)
        except Exception as exc:
            await sandbox.record_action(
                ActionType.KEYBOARD_TYPE, params=action_params, error=str(exc)
            )
            return ToolResult(
                title="Keyboard error",
                output=f"Keyboard action failed: {exc}",
                error=True,
            )

        action_type_map = {
            "type": ActionType.KEYBOARD_TYPE,
            "press": ActionType.KEYBOARD_PRESS,
            "hotkey": ActionType.KEYBOARD_HOTKEY,
        }
        await sandbox.record_action(
            action_type_map.get(params.action, ActionType.KEYBOARD_TYPE),
            params=action_params,
            result=result_msg,
        )

        return ToolResult(title=f"Keyboard: {params.action}", output=result_msg)

    @staticmethod
    def _do_action(params: "KeyboardTool.Params") -> str:
        """Synchronous pyautogui calls — runs in thread pool."""
        import time
        _check_accessibility()
        pag = _pyautogui()
        settle = params.settle_ms / 1000.0

        if params.action == "type":
            if not params.text:
                raise ValueError("text is required for action='type'.")
            _type_text(pag, params.text, params.interval)
            time.sleep(settle)
            preview = params.text[:40] + ("…" if len(params.text) > 40 else "")
            return f"Typed {len(params.text)} characters: {preview!r}"

        if params.action == "press":
            if not params.key:
                raise ValueError("key is required for action='press'.")
            pag.press(params.key)
            time.sleep(settle)
            return f"Pressed key: {params.key!r}"

        if params.action == "hotkey":
            if not params.keys:
                raise ValueError("keys list is required for action='hotkey'.")
            pag.hotkey(*params.keys)
            time.sleep(settle)
            combo = "+".join(params.keys)
            return f"Pressed hotkey: {combo}"

        raise ValueError(f"Unknown keyboard action: {params.action!r}")
