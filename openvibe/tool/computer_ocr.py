"""OCRTool — extract visible text from a screen region without LLM vision.

Useful when you need the exact string content of on-screen text for:
  - Verification ("does the error message say X?")
  - Data extraction (table cells, form values, log lines)
  - Copy-paste without manual selection

Backends: pytesseract → macOS Vision → Windows WinRT → install hint.
See :mod:`openvibe.computer.ocr` for backend details.
"""

from __future__ import annotations

from __future__ import annotations

import asyncio

from pydantic import Field

from openvibe.tool.base import Tool, ToolContext, ToolResult


class OCRTool(Tool):
    """Extract visible text from a screen region using OCR."""

    name = "ocr"
    description = (
        "Extract visible text from the screen or a sub-region as a machine-readable "
        "string. Use this when you need the exact text of labels, error messages, "
        "table values, or any UI element that contains text you need to reason about "
        "or act on precisely. Provide a region to focus on a specific area."
    )

    class Params(Tool.Params):
        region: list[int] | None = Field(
            default=None,
            description=(
                "Screen region as [x, y, width, height] in logical pixels. "
                "Omit (or pass null) to OCR the full primary screen. "
                "Use the same coordinate space as the screenshot tool."
            ),
        )

    async def execute(self, ctx: ToolContext, params: "OCRTool.Params") -> ToolResult:  # type: ignore[override]
        from openvibe.computer.sandbox import ActionType, get_sandbox

        region_desc = f" region {params.region}" if params.region else " full screen"
        await ctx.check_permission(
            tool="ocr",
            argument=f"ocr{region_desc}",
            description=f"Extract text via OCR from{region_desc}",
        )

        sandbox = get_sandbox(ctx.session_id)

        region: tuple[int, int, int, int] | None = None
        if params.region:
            if len(params.region) != 4:
                return ToolResult(
                    title="OCR error",
                    output="region must have exactly 4 elements: [x, y, width, height]",
                    error=True,
                )
            region = (
                params.region[0], params.region[1],
                params.region[2], params.region[3],
            )

        loop = asyncio.get_event_loop()
        try:
            from openvibe.computer.ocr import extract_text_from_region
            text = await loop.run_in_executor(None, extract_text_from_region, region)
        except Exception as exc:
            await sandbox.record_action(
                ActionType.OCR,
                params={"region": params.region},
                error=str(exc),
            )
            return ToolResult(
                title="OCR error",
                output=f"OCR extraction failed: {exc}",
                error=True,
            )

        await sandbox.record_action(
            ActionType.OCR,
            params={"region": params.region},
            result=f"{len(text)} chars extracted",
        )

        return ToolResult(
            title=f"OCR{region_desc}",
            output=text or "(no text detected)",
            metadata={"truncated": True} if len(text) > 4000 else {},
        )
