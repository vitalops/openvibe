"""Trajectory data model for learn recordings."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TrajectoryEvent:
    timestamp: float
    action_type: str  # "click", "right_click", "scroll", "type", "key"
    x: int | None = None
    y: int | None = None
    button: str | None = None   # "left", "right", "middle"
    text: str | None = None     # for "type" events
    key: str | None = None      # for "key" / modifier-combo events
    scroll_dx: int = 0
    scroll_dy: int = 0
    screenshot_after: str | None = None  # base64 JPEG, set async after event


@dataclass
class Trajectory:
    task_name: str
    events: list[TrajectoryEvent] = field(default_factory=list)
    initial_screenshot: str | None = None  # base64 JPEG of screen before first action
    started_at: float = field(default_factory=time.time)
    stopped_at: float = 0.0


# ---------------------------------------------------------------------------
# Content builder  (multimodal — text + image blocks, no base64 in text)
# ---------------------------------------------------------------------------

_MAX_SCREENSHOTS = 6
_SNAP_TYPES = frozenset({"click", "right_click"})
_SNAP_KEYS = frozenset({"return", "enter", "escape"})


def build_summarization_content(
    trajectory: Trajectory, procedure_path: str
) -> list[dict]:
    """Return a list of content blocks (text + image_url) for a multimodal LLM call.

    Images are sent as proper image_url blocks — never embedded as base64 inside
    text — so they never appear in chat logs or anywhere visible to the user.
    """
    blocks: list[dict] = []

    def _text(t: str) -> dict:
        return {"type": "text", "text": t}

    def _image(b64: str) -> dict:
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        }

    blocks.append(
        _text(
            f"I recorded a computer task called '{trajectory.task_name}'.\n\n"
            "Please:\n"
            "1. Analyse the screenshots and action sequence below.\n"
            "2. Understand what task was performed and how.\n"
            f"3. Return ONLY a JSON object (no markdown fences, no explanation) with this structure:\n"
            "{\n"
            f'  "task_name": "{trajectory.task_name}",\n'
            '  "description": "one-line summary",\n'
            '  "steps": ["step 1", "step 2", "..."],\n'
            '  "procedure": "Full natural language procedure. Describe UI elements by '
            "label or role, not pixel coordinates. Make it robust to minor UI changes.\"\n"
            "}\n\n"
            "--- RECORDING ---"
        )
    )

    if trajectory.initial_screenshot:
        blocks.append(_text("Initial screen state:"))
        blocks.append(_image(trajectory.initial_screenshot))

    screenshot_count = 0
    for i, ev in enumerate(trajectory.events):
        elapsed = ev.timestamp - trajectory.started_at
        blocks.append(_text(f"Step {i + 1} (+{elapsed:.1f}s): {_describe(ev)}"))

        want = (
            ev.screenshot_after is not None
            and screenshot_count < _MAX_SCREENSHOTS
            and (
                ev.action_type in _SNAP_TYPES
                or (ev.action_type == "key" and ev.key in _SNAP_KEYS)
            )
        )
        if want:
            blocks.append(_image(ev.screenshot_after))  # type: ignore[arg-type]
            screenshot_count += 1

    blocks.append(_text("--- END OF RECORDING ---\n\nReturn only the JSON object."))
    return blocks


def build_display_summary(trajectory: Trajectory) -> str:
    """Return a clean, human-readable summary of the trajectory for chat display.

    Never includes base64 or any binary data.
    """
    duration = (trajectory.stopped_at or time.time()) - trajectory.started_at
    n_clicks = sum(1 for e in trajectory.events if e.action_type in _SNAP_TYPES)
    n_keys = sum(1 for e in trajectory.events if e.action_type in ("key", "type"))
    n_ss = sum(1 for e in trajectory.events if e.screenshot_after is not None)

    lines = [
        f"[bold]Task:[/bold] {trajectory.task_name}",
        f"[dim]Duration: {duration:.1f}s  ·  "
        f"{len(trajectory.events)} events  ·  "
        f"{n_clicks} clicks  ·  {n_keys} keyboard events  ·  "
        f"{n_ss} screenshots captured[/dim]",
        "",
        "[bold]Event log:[/bold]",
    ]
    for i, ev in enumerate(trajectory.events):
        elapsed = ev.timestamp - trajectory.started_at
        lines.append(f"  {i + 1:2d}. (+{elapsed:5.1f}s) {_describe(ev)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _describe(ev: TrajectoryEvent) -> str:
    if ev.action_type == "click":
        return f"Left-click at ({ev.x}, {ev.y})"
    if ev.action_type == "right_click":
        return f"Right-click at ({ev.x}, {ev.y})"
    if ev.action_type == "scroll":
        direction = "down" if ev.scroll_dy < 0 else "up"
        return f"Scroll {direction} at ({ev.x}, {ev.y})"
    if ev.action_type == "type":
        return f"Typed: {ev.text!r}"
    if ev.action_type == "key":
        return f"Key: {ev.key}"
    return f"Action: {ev.action_type}"
