"""ScreenshotTool — capture the current screen.

The LLM receives a base-64 PNG attachment so it can *see* the current state
of the desktop before deciding which action to take next.

Enhanced with SOTA features:
- ``show_cursor`` — overlays a red dot at the current cursor position
  (mirrors the visual style in Anthropic's computer-use reference demo).
- ``marks`` — Set-of-Marks (SoM) overlay: draws numbered bounding boxes
  over interactive UI elements so the model can reference them by number
  instead of computing raw pixel coordinates (Yang et al. 2023; OmniParser).
- ``zoom`` — crops a [x0, y0, x1, y1] region and returns it at higher
  relative resolution, matching the ``zoom`` action in Anthropic's
  computer_20251124 tool spec.
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
        "that previous actions had the intended effect.\n"
        "Options:\n"
        "  show_cursor=true  — draw a red dot at the current cursor position\n"
        "  marks=true        — overlay numbered boxes on all interactive elements "
        "(Set-of-Marks); the tool output lists each mark so you can say "
        "\"click mark 3\" instead of guessing pixel coordinates\n"
        "  zoom=[x0,y0,x1,y1] — return a cropped close-up of that screen region "
        "(logical pixels); use after a full screenshot to inspect small text\n"
        "  save_path — write the PNG to disk"
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
        show_cursor: bool = Field(
            default=False,
            description=(
                "When true, overlays a red dot at the current cursor position on the "
                "screenshot. Useful for confirming where the pointer is before clicking."
            ),
        )
        marks: bool = Field(
            default=False,
            description=(
                "When true, draws numbered bounding boxes (Set-of-Marks / SoM) over "
                "all interactive UI elements in the frontmost application window. "
                "The tool output includes a numbered list so you can click by saying "
                "mouse action at the centre coordinate of a mark rather than guessing. "
                "Uses the platform accessibility API (AppleScript / AT-SPI / UI Automation)."
            ),
        )
        zoom: list[int] | None = Field(
            default=None,
            description=(
                "Optional crop region as [x0, y0, x1, y1] in logical screen pixels. "
                "Returns a zoomed-in view of that sub-region. Use after a full screenshot "
                "to inspect small text, icons, or crowded UI areas more clearly. "
                "Equivalent to the 'zoom' action in Anthropic's computer_20251124 spec."
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

        # ── zoom: [x0, y0, x1, y1] → convert to capture region (x, y, w, h) ──
        capture_region: tuple[int, int, int, int] | None = None
        if params.zoom:
            if len(params.zoom) != 4:
                return ToolResult(
                    title="Screenshot error",
                    output="zoom must have exactly 4 elements: [x0, y0, x1, y1]",
                    error=True,
                )
            x0, y0, x1, y1 = params.zoom
            capture_region = (x0, y0, x1 - x0, y1 - y0)
        elif params.region:
            if len(params.region) != 4:
                return ToolResult(
                    title="Screenshot error",
                    output="region must have exactly 4 elements: [x, y, width, height]",
                    error=True,
                )
            capture_region = (
                params.region[0], params.region[1],
                params.region[2], params.region[3],
            )

        sandbox = get_sandbox(ctx.session_id)
        if capture_region and not sandbox.is_coordinate_allowed(
            capture_region[0], capture_region[1]
        ):
            return ToolResult(
                title="Screenshot denied",
                output="The requested region is outside the permitted screen area.",
                error=True,
            )

        try:
            loop = asyncio.get_event_loop()
            png_bytes, width, height = await loop.run_in_executor(
                None, capture_screen, capture_region
            )
        except ImportError as exc:
            return ToolResult(title="Screenshot error", output=str(exc), error=True)
        except Exception as exc:
            await sandbox.record_action(
                ActionType.SCREENSHOT,
                params={"region": params.region, "zoom": params.zoom},
                error=str(exc),
            )
            return ToolResult(
                title="Screenshot error",
                output=f"Failed to capture screenshot: {exc}",
                error=True,
            )

        # ── get logical screen size for Retina scale computation ──────────────
        logical_w: int | None = None
        logical_h: int | None = None
        try:
            import pyautogui  # type: ignore[import-not-found]
            logical_w, logical_h = pyautogui.size()
        except Exception:
            pass

        scale_x = (width / logical_w) if logical_w else 1.0
        scale_y = (height / logical_h) if logical_h else 1.0

        # ── optional overlays (SoM marks and cursor) ──────────────────────────
        marks_summary: str | None = None
        if params.marks or params.show_cursor:
            try:
                from PIL import Image  # type: ignore[import-not-found]
                import io as _io
                pil_img = Image.open(_io.BytesIO(png_bytes))

                if params.marks:
                    from openvibe.computer.marks import (
                        draw_som_marks, get_interactive_elements,
                    )
                    elements = await loop.run_in_executor(
                        None, get_interactive_elements, None
                    )
                    pil_img, _mark_map, marks_summary = draw_som_marks(
                        pil_img, elements, scale_x, scale_y
                    )

                if params.show_cursor:
                    from openvibe.computer.marks import overlay_cursor
                    try:
                        import pyautogui  # type: ignore[import-not-found]
                        cx, cy = pyautogui.position()
                        pil_img = overlay_cursor(pil_img, cx, cy, scale_x, scale_y)
                    except Exception:
                        pass  # cursor overlay is best-effort

                # Re-encode annotated image
                buf = _io.BytesIO()
                pil_img.save(buf, format="PNG", optimize=True)
                png_bytes = buf.getvalue()
                width, height = pil_img.size
            except ImportError:
                pass  # Pillow not installed — skip overlays silently
            except Exception:
                pass  # never block screenshot on overlay failure

        # ── diff vs. previous screenshot ──────────────────────────────────────
        diff_summary: str | None = None
        if sandbox.last_screenshot is not None and not params.zoom:
            try:
                from openvibe.computer.capture import diff_screenshots
                diff = await loop.run_in_executor(
                    None, diff_screenshots, sandbox.last_screenshot, png_bytes
                )
                diff_summary = diff["summary"]  # type: ignore[index]
            except Exception:
                pass

        sandbox.last_screenshot = png_bytes

        await sandbox.record_action(
            ActionType.SCREENSHOT,
            params={"region": params.region, "zoom": params.zoom,
                    "marks": params.marks, "show_cursor": params.show_cursor},
            result=f"{width}x{height}" + (f" | {diff_summary}" if diff_summary else ""),
        )

        # ── optional save to disk ─────────────────────────────────────────────
        saved_path: str | None = None
        if params.save_path:
            try:
                dest = os.path.expanduser(params.save_path)
                os.makedirs(
                    os.path.dirname(dest) if os.path.dirname(dest) else ".",
                    exist_ok=True,
                )
                with open(dest, "wb") as fh:
                    fh.write(png_bytes)
                saved_path = dest
            except Exception as exc:
                return ToolResult(
                    title="Screenshot save error",
                    output=(
                        f"Screenshot captured ({width}×{height}) but could not be "
                        f"saved to {params.save_path!r}: {exc}"
                    ),
                    attachments=[
                        Attachment(
                            filename="screenshot.png",
                            content=png_bytes,
                            media_type="image/png",
                        )
                    ],
                    metadata={"width": width, "height": height},
                    error=True,
                )

        # ── build output text ─────────────────────────────────────────────────
        zoom_desc = f" (zoom {params.zoom})" if params.zoom else ""
        region_desc = f" (region {params.region})" if params.region and not params.zoom else ""
        save_desc = f" → saved to {saved_path}" if saved_path else ""
        logical_note = (
            f" (logical screen: {logical_w}×{logical_h})"
            if logical_w and logical_h else ""
        )

        output_lines = [
            f"Captured {width}×{height} screenshot{zoom_desc}{region_desc}"
            f"{save_desc}.{logical_note}",
            f"Mouse coordinates: pass image_width={width}, image_height={height} "
            "to the mouse tool for correct Retina scaling.",
        ]
        if diff_summary:
            output_lines.append(
                f"Change detection vs previous screenshot: {diff_summary}"
            )
        if marks_summary:
            output_lines.append(
                f"\nSet-of-Marks — interactive elements detected:\n{marks_summary}"
            )
        if params.show_cursor and logical_w:
            try:
                import pyautogui  # type: ignore[import-not-found]
                cx, cy = pyautogui.position()
                output_lines.append(f"Cursor position (logical): ({cx}, {cy})")
            except Exception:
                pass

        return ToolResult(
            title=f"Screenshot {width}×{height}{zoom_desc}{region_desc}",
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
