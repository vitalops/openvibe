"""MouseTool — control the mouse pointer.

Supports click, double-click, right-click, move, scroll, and drag actions.
All coordinates are validated against the session sandbox before execution.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import Field

from openvibe.tool.base import Tool, ToolContext, ToolResult

# pyautogui import is deferred so the rest of openvibe loads even when the
# computer-use extras are not installed.


def _pyautogui():  # type: ignore[return]
    try:
        import pyautogui  # type: ignore[import-not-found]
        return pyautogui
    except ImportError as exc:
        raise ImportError(
            "pyautogui is required for computer use: pip install pyautogui"
        ) from exc


def _check_accessibility() -> None:
    """Raise RuntimeError if macOS Accessibility permission is not granted.

    pyautogui silently does nothing (no exception) when Accessibility is denied.
    This gives a clear, actionable error instead.
    """
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
            "macOS Accessibility permission is required for mouse/keyboard control. "
            "Go to System Settings → Privacy & Security → Accessibility and add your "
            "terminal application (e.g. iTerm, Terminal, VS Code)."
        )


class MouseTool(Tool):
    """Move, click, scroll, or drag the mouse pointer."""

    name = "mouse"
    description = (
        "Control the mouse: move to a position, click (left/right/middle), "
        "double-click, scroll, or drag from one point to another. "
        "Coordinates are in screen pixels with (0,0) at the top-left corner."
    )

    class Params(Tool.Params):
        action: Literal["move", "click", "double_click", "right_click", "scroll", "drag"] = Field(
            description=(
                "Mouse action to perform:\n"
                "  move         — move pointer to (x, y) without clicking\n"
                "  click        — left-click at (x, y)\n"
                "  double_click — double left-click at (x, y)\n"
                "  right_click  — right-click at (x, y)\n"
                "  scroll       — scroll wheel at (x, y) by 'amount' ticks\n"
                "  drag         — drag from (x, y) to (end_x, end_y)"
            )
        )
        x: int = Field(description="X coordinate as it appears in the screenshot image.")
        y: int = Field(description="Y coordinate as it appears in the screenshot image.")
        end_x: int | None = Field(
            default=None,
            description="Target X coordinate for drag action (image pixels).",
        )
        end_y: int | None = Field(
            default=None,
            description="Target Y coordinate for drag action (image pixels).",
        )
        image_width: int | None = Field(
            default=None,
            description=(
                "Width of the screenshot image the coordinates were read from. "
                "ALWAYS provide this — it is used to translate image pixels to "
                "logical screen coordinates, fixing Retina/HiDPI scaling. "
                "Use the width reported by the screenshot tool output."
            ),
        )
        image_height: int | None = Field(
            default=None,
            description=(
                "Height of the screenshot image. Provide alongside image_width."
            ),
        )
        amount: int = Field(
            default=3,
            description="Scroll ticks (positive = up/right, negative = down/left). Used only for scroll.",
        )
        duration: float = Field(
            default=0.25,
            description="Movement duration in seconds (for smooth animation).",
        )
        settle_ms: int = Field(
            default=500,
            description=(
                "Milliseconds to wait after the action for the UI to settle before returning. "
                "Increase to 1000–2000 for slow apps or animations. Default 500."
            ),
        )

    async def execute(self, ctx: ToolContext, params: "MouseTool.Params") -> ToolResult:  # type: ignore[override]
        from openvibe.computer.sandbox import ActionType, get_sandbox

        # ── Retina / HiDPI coordinate scaling ───────────────────────────────
        # Screenshots are downscaled to ≤1920px wide, but pyautogui works in
        # logical screen pixels (e.g. 1440×900 on a Retina MacBook).
        # If the caller supplies the image dimensions we read from the
        # screenshot tool output, we translate automatically — no guessing.
        scaled_params = params
        scale_note = ""
        if params.image_width and params.image_height:
            try:
                pag = _pyautogui()
                screen_w, screen_h = pag.size()
                sx = screen_w / params.image_width
                sy = screen_h / params.image_height
                if abs(sx - 1.0) > 0.02 or abs(sy - 1.0) > 0.02:
                    # Rebuild params with scaled coordinates
                    scaled = params.model_copy(update={
                        "x": round(params.x * sx),
                        "y": round(params.y * sy),
                        "end_x": round(params.end_x * sx) if params.end_x is not None else None,
                        "end_y": round(params.end_y * sy) if params.end_y is not None else None,
                    })
                    scaled_params = scaled
                    scale_note = (
                        f" [image ({params.x},{params.y}) → logical ({scaled.x},{scaled.y})"
                        f" scale {sx:.3f}×{sy:.3f}]"
                    )
            except Exception:
                pass  # scaling is best-effort; never block the action
        # ─────────────────────────────────────────────────────────────────────

        action_label = f"mouse {params.action} at ({params.x}, {params.y})"
        await ctx.check_permission(
            tool="mouse",
            argument=action_label,
            description=f"Perform mouse action: {action_label}",
        )

        sandbox = get_sandbox(ctx.session_id)
        if not sandbox.is_coordinate_allowed(scaled_params.x, scaled_params.y):
            return ToolResult(
                title="Mouse action denied",
                output=f"Coordinate ({scaled_params.x}, {scaled_params.y}) is outside the permitted screen region.",
                error=True,
            )

        action_params: dict = {
            "action": params.action,
            "x": params.x,
            "y": params.y,
            "amount": params.amount,
            "duration": params.duration,
        }
        if params.action == "drag":
            action_params["end_x"] = params.end_x
            action_params["end_y"] = params.end_y

        try:
            loop = asyncio.get_event_loop()
            result_msg = await loop.run_in_executor(
                None, self._do_action, scaled_params
            )
        except ImportError as exc:
            return ToolResult(title="Mouse error", output=str(exc), error=True)
        except Exception as exc:
            await sandbox.record_action(
                ActionType.MOUSE_CLICK, params=action_params, error=str(exc)
            )
            return ToolResult(
                title="Mouse error",
                output=f"Mouse action failed: {exc}",
                error=True,
            )

        action_type_map = {
            "move": ActionType.MOUSE_MOVE,
            "click": ActionType.MOUSE_CLICK,
            "double_click": ActionType.MOUSE_CLICK,
            "right_click": ActionType.MOUSE_CLICK,
            "scroll": ActionType.MOUSE_SCROLL,
            "drag": ActionType.MOUSE_DRAG,
        }
        await sandbox.record_action(
            action_type_map.get(params.action, ActionType.MOUSE_CLICK),
            params=action_params,
            result=result_msg,
        )

        return ToolResult(
            title=f"Mouse: {params.action}",
            output=result_msg + scale_note,
        )

    @staticmethod
    def _do_action(params: "MouseTool.Params") -> str:
        """Synchronous pyautogui calls — runs in thread pool."""
        import time
        _check_accessibility()
        pag = _pyautogui()
        pag.FAILSAFE = True  # move to corner to abort

        settle = params.settle_ms / 1000.0

        if params.action == "move":
            pag.moveTo(params.x, params.y, duration=params.duration)
            time.sleep(settle)
            return f"Moved mouse to ({params.x}, {params.y})."

        if params.action == "click":
            pag.click(params.x, params.y, duration=params.duration)
            time.sleep(settle)
            return f"Left-clicked at ({params.x}, {params.y})."

        if params.action == "double_click":
            pag.doubleClick(params.x, params.y, duration=params.duration)
            time.sleep(settle)
            return f"Double-clicked at ({params.x}, {params.y})."

        if params.action == "right_click":
            pag.rightClick(params.x, params.y, duration=params.duration)
            time.sleep(settle)
            return f"Right-clicked at ({params.x}, {params.y})."

        if params.action == "scroll":
            pag.scroll(params.amount, x=params.x, y=params.y)
            time.sleep(settle)
            direction = "up" if params.amount > 0 else "down"
            return f"Scrolled {direction} by {abs(params.amount)} ticks at ({params.x}, {params.y})."

        if params.action == "drag":
            if params.end_x is None or params.end_y is None:
                raise ValueError("end_x and end_y are required for drag action.")
            pag.moveTo(params.x, params.y, duration=0.1)
            pag.dragTo(params.end_x, params.end_y, duration=params.duration, mouseDownUp=True)
            time.sleep(params.settle_ms / 1000.0)
            return (
                f"Dragged from ({params.x}, {params.y}) "
                f"to ({params.end_x}, {params.end_y})."
            )

        raise ValueError(f"Unknown mouse action: {params.action!r}")
