"""ScreenshotTool — capture the current screen.

The LLM receives a base-64 PNG attachment so it can *see* the current state
of the desktop before deciding which action to take next.
"""

from __future__ import annotations

import asyncio
import os

from pydantic import Field

from openvibe.tool.base import Attachment, Tool, ToolContext, ToolResult


class ScreenshotTool(Tool):
    """Capture a screenshot of the full screen or a specific region."""

    name = "screenshot"
    description = (
        "Capture a screenshot of the current screen or a sub-region. "
        "Returns the image so you can observe the current UI state before "
        "deciding which action to take next. Call this frequently to verify "
        "that previous actions had the intended effect. "
        "Use save_path to write the PNG directly to disk (e.g. '/Users/you/Desktop/shot.png')."
    )

    class Params(Tool.Params):
        region: list[int] | None = Field(
            default=None,
            description=(
                "Optional screen region to capture as [x, y, width, height] in pixels. "
                "Omit (or pass null) to capture the entire primary screen."
            ),
        )
        save_path: str | None = Field(
            default=None,
            description=(
                "Optional absolute path where the PNG should be saved on disk. "
                "Parent directories are created automatically. "
                "Example: '/Users/alice/Documents/screenshot.png'"
            ),
        )

    async def execute(self, ctx: ToolContext, params: "ScreenshotTool.Params") -> ToolResult:  # type: ignore[override]
        from openvibe.computer.capture import capture_screen
        from openvibe.computer.sandbox import ActionType, get_sandbox

        await ctx.check_permission(
            tool="screenshot",
            argument="capture screen",
            description="Take a screenshot of the current screen",
        )

        region: tuple[int, int, int, int] | None = None
        if params.region:
            if len(params.region) != 4:
                return ToolResult(
                    title="Screenshot error",
                    output="region must have exactly 4 elements: [x, y, width, height]",
                    error=True,
                )
            region = (params.region[0], params.region[1], params.region[2], params.region[3])

        sandbox = get_sandbox(ctx.session_id)
        if region and not sandbox.is_coordinate_allowed(region[0], region[1]):
            return ToolResult(
                title="Screenshot denied",
                output="The requested region is outside the permitted screen area.",
                error=True,
            )

        try:
            loop = asyncio.get_event_loop()
            png_bytes, width, height = await loop.run_in_executor(
                None, capture_screen, region
            )
        except ImportError as exc:
            return ToolResult(
                title="Screenshot error",
                output=str(exc),
                error=True,
            )
        except Exception as exc:
            await sandbox.record_action(
                ActionType.SCREENSHOT,
                params={"region": params.region},
                error=str(exc),
            )
            return ToolResult(
                title="Screenshot error",
                output=f"Failed to capture screenshot: {exc}",
                error=True,
            )

        # Compute diff against the previous screenshot stored in the sandbox.
        # This gives the LLM concrete change-detection feedback rather than
        # requiring it to visually compare two images in its head.
        diff_summary: str | None = None
        if sandbox.last_screenshot is not None:
            try:
                from openvibe.computer.capture import diff_screenshots
                loop = asyncio.get_event_loop()
                diff = await loop.run_in_executor(
                    None, diff_screenshots, sandbox.last_screenshot, png_bytes
                )
                diff_summary = diff["summary"]  # type: ignore[index]
            except Exception:
                pass  # diff is best-effort; never block the screenshot

        # Update the stored baseline for the next comparison.
        sandbox.last_screenshot = png_bytes

        await sandbox.record_action(
            ActionType.SCREENSHOT,
            params={"region": params.region},
            result=f"{width}x{height}" + (f" | {diff_summary}" if diff_summary else ""),
        )

        # Save to disk if a path was requested.
        saved_path: str | None = None
        if params.save_path:
            try:
                dest = os.path.expanduser(params.save_path)
                os.makedirs(os.path.dirname(dest) if os.path.dirname(dest) else ".", exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(png_bytes)
                saved_path = dest
            except Exception as exc:
                return ToolResult(
                    title="Screenshot save error",
                    output=f"Screenshot captured ({width}×{height}) but could not be saved to {params.save_path!r}: {exc}",
                    attachments=[
                        Attachment(filename="screenshot.png", content=png_bytes, media_type="image/png")
                    ],
                    metadata={"width": width, "height": height},
                    error=True,
                )

        region_desc = f" (region {params.region})" if params.region else ""
        save_desc = f" → saved to {saved_path}" if saved_path else ""

        # Include logical screen size so the mouse tool can scale correctly.
        logical_note = ""
        try:
            import pyautogui  # type: ignore[import-not-found]
            lw, lh = pyautogui.size()
            logical_note = f" (logical screen: {lw}×{lh})"
        except Exception:
            pass

        output_lines = [
            f"Captured {width}×{height} screenshot{region_desc}{save_desc}.{logical_note}",
            f"Mouse coordinates: pass image_width={width}, image_height={height} to the mouse tool for correct Retina scaling.",
        ]
        if diff_summary:
            output_lines.append(f"Change detection vs previous screenshot: {diff_summary}")

        return ToolResult(
            title=f"Screenshot {width}×{height}{region_desc}",
            output="\n".join(output_lines),
            attachments=[
                Attachment(
                    filename="screenshot.png",
                    content=png_bytes,
                    media_type="image/png",
                )
            ],
            metadata={"width": width, "height": height, "truncated": True},
        )
