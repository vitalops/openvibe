"""ClipboardTool — read and write the system clipboard.

Uses platform-native tools for zero-dependency operation:
  macOS   — pbpaste / pbcopy (built-in)
  Linux   — xclip (preferred) / xsel / pyperclip fallback
  Windows — pyperclip (auto-installed via deps helper)

Unicode is fully supported on all platforms via clipboard-paste strategy.
"""

from __future__ import annotations

from __future__ import annotations

import asyncio
import platform
import subprocess
from typing import Literal

from pydantic import Field

from openvibe.tool.base import Tool, ToolContext, ToolResult

_PLATFORM = platform.system()


class ClipboardTool(Tool):
    """Read or write the system clipboard."""

    name = "clipboard"
    description = (
        "Read the current clipboard text or write new text to the clipboard. "
        "Use 'read' to retrieve what the user (or a previous action) has copied; "
        "use 'write' to place text on the clipboard so you can paste it into an "
        "application with keyboard 'hotkey' Ctrl/Cmd+V."
    )

    class Params(Tool.Params):
        action: Literal["read", "write"] = Field(
            description=(
                "Clipboard action:\n"
                "  read  — return the current clipboard text contents\n"
                "  write — set the clipboard to the provided text value"
            )
        )
        text: str | None = Field(
            default=None,
            description="Text to place on the clipboard. Required for action='write'.",
        )

    async def execute(self, ctx: ToolContext, params: "ClipboardTool.Params") -> ToolResult:  # type: ignore[override]
        from openvibe.computer.sandbox import ActionType, get_sandbox

        if params.action == "write":
            arg_desc = f"clipboard write: {(params.text or '')[:40]!r}"
        else:
            arg_desc = "clipboard read"

        await ctx.check_permission(
            tool="clipboard",
            argument=arg_desc,
            description=f"Clipboard operation: {arg_desc}",
        )

        sandbox = get_sandbox(ctx.session_id)
        loop = asyncio.get_event_loop()

        try:
            result_msg = await loop.run_in_executor(None, self._do_action, params)
        except Exception as exc:
            action_type = (
                ActionType.CLIPBOARD_READ
                if params.action == "read"
                else ActionType.CLIPBOARD_WRITE
            )
            await sandbox.record_action(
                action_type, params={"action": params.action}, error=str(exc)
            )
            return ToolResult(title="Clipboard error", output=str(exc), error=True)

        action_type = (
            ActionType.CLIPBOARD_READ
            if params.action == "read"
            else ActionType.CLIPBOARD_WRITE
        )
        await sandbox.record_action(
            action_type,
            params={"action": params.action, "text_len": len(params.text or "")},
            result=result_msg[:200],
        )
        return ToolResult(title=f"Clipboard: {params.action}", output=result_msg)

    @staticmethod
    def _do_action(params: "ClipboardTool.Params") -> str:
        if params.action == "read":
            return _read_clipboard()
        if params.action == "write":
            if not params.text:
                raise ValueError("'text' is required for action='write'.")
            _write_clipboard(params.text)
            preview = params.text[:80] + ("…" if len(params.text) > 80 else "")
            return f"Clipboard set ({len(params.text)} chars): {preview!r}"
        raise ValueError(f"Unknown clipboard action: {params.action!r}")


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------


def _read_clipboard() -> str:
    if _PLATFORM == "Darwin":
        r = subprocess.run(["pbpaste"], capture_output=True, timeout=5)
        if r.returncode != 0:
            raise RuntimeError(f"pbpaste failed: {r.stderr.decode(errors='replace')}")
        text = r.stdout.decode("utf-8", errors="replace")
        return text if text else "(clipboard is empty)"

    if _PLATFORM == "Linux":
        for cmd in (
            ["xclip", "-selection", "clipboard", "-o"],
            ["xsel", "--clipboard", "--output"],
        ):
            r = subprocess.run(cmd, capture_output=True, timeout=5)
            if r.returncode == 0:
                text = r.stdout.decode("utf-8", errors="replace")
                return text if text else "(clipboard is empty)"
        # Fallback: pyperclip
        try:
            import pyperclip  # type: ignore[import-not-found]
            t = pyperclip.paste()
            return t if t else "(clipboard is empty)"
        except ImportError:
            raise RuntimeError(
                "Clipboard read requires xclip, xsel, or pyperclip.\n"
                "  sudo apt install xclip   # Debian/Ubuntu\n"
                "  sudo dnf install xclip   # Fedora\n"
                "  pip install pyperclip    # cross-platform fallback"
            )

    if _PLATFORM == "Windows":
        try:
            import pyperclip  # type: ignore[import-not-found]
        except ImportError:
            from openvibe.computer.deps import ensure_import
            pyperclip = ensure_import("pyperclip")
        t = pyperclip.paste()
        return t if t else "(clipboard is empty)"

    raise RuntimeError(f"Clipboard not supported on platform: {_PLATFORM!r}")


def _write_clipboard(text: str) -> None:
    if _PLATFORM == "Darwin":
        r = subprocess.run(
            ["pbcopy"], input=text.encode("utf-8"), capture_output=True, timeout=5
        )
        if r.returncode != 0:
            raise RuntimeError(f"pbcopy failed: {r.stderr.decode(errors='replace')}")
        return

    if _PLATFORM == "Linux":
        for cmd in (
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ):
            r = subprocess.run(
                cmd, input=text.encode("utf-8"), capture_output=True, timeout=5
            )
            if r.returncode == 0:
                return
        try:
            import pyperclip  # type: ignore[import-not-found]
            pyperclip.copy(text)
            return
        except ImportError:
            raise RuntimeError(
                "Clipboard write requires xclip, xsel, or pyperclip.\n"
                "  sudo apt install xclip\n"
                "  pip install pyperclip"
            )

    if _PLATFORM == "Windows":
        try:
            import pyperclip  # type: ignore[import-not-found]
        except ImportError:
            from openvibe.computer.deps import ensure_import
            pyperclip = ensure_import("pyperclip")
        pyperclip.copy(text)
        return

    raise RuntimeError(f"Clipboard not supported on platform: {_PLATFORM!r}")
