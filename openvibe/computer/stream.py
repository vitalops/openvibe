"""Async screen-update streaming for real-time computer-use feedback.

The stream yields base-64 PNG frames at a configurable frame rate.  Frames
are captured in a thread-pool executor so the event loop stays responsive.

Example::

    import asyncio
    from openvibe.computer.stream import stream_screen

    async def watch():
        stop = asyncio.Event()
        async for b64, w, h in stream_screen(fps=4, stop_event=stop):
            print(f"frame {w}x{h}, {len(b64)} chars")
            # … forward to TUI / websocket …
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


async def stream_screen(
    fps: float = 2.0,
    region: tuple[int, int, int, int] | None = None,
    stop_event: asyncio.Event | None = None,
    deduplicate: bool = True,
) -> AsyncIterator[tuple[str, int, int]]:
    """Yield ``(base64_png, width, height)`` frames at *fps*.

    Parameters
    ----------
    fps:
        Target frames per second.  Values above ~10 may not be achievable
        on slower machines; the loop will simply run as fast as it can.
    region:
        Optional ``(x, y, width, height)`` capture region.
    stop_event:
        Set this :class:`asyncio.Event` to terminate the generator cleanly.
    deduplicate:
        When ``True`` (default), identical consecutive frames are skipped
        to reduce bandwidth.
    """
    from openvibe.computer.capture import capture_screen_b64

    interval = 1.0 / max(fps, 0.1)
    loop = asyncio.get_event_loop()
    last_b64: str | None = None

    while True:
        if stop_event is not None and stop_event.is_set():
            return

        b64, w, h = await loop.run_in_executor(None, capture_screen_b64, region)

        if deduplicate and b64 == last_b64:
            await asyncio.sleep(interval)
            continue

        last_b64 = b64
        yield b64, w, h

        await asyncio.sleep(interval)
