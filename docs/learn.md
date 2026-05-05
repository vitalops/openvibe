# Learn & Replay

Learn lets you record any computer task once and replay it autonomously later. The agent derives its replay instructions from the macOS Accessibility tree captured during recording — not from pixel coordinates or application-specific knowledge.

## Installation

```bash
pip install "openvibe[learn]"
```

This installs:
- `pynput` — global mouse and keyboard listener
- `atomacos` — macOS Accessibility API (macOS only)

## Recording a task

```
/learn start "export monthly report"
```

Immediately starts a global recording session. You can use any application normally — openvibe captures everything in the background.

During recording, for every significant action:
- **Mouse click** — position, button, screenshot after the click
- **Keyboard input** — buffered into type events (flushed after 0.55s inactivity or before any special key)
- **Key combos** — e.g. `cmd+s`, `ctrl+z` recorded individually
- **Special keys** — `return`, `escape`, `tab`, `backspace`, arrow keys, function keys
- **Scroll** — position and direction (consecutive scrolls at the same position are consolidated)
- **Accessibility context** — for every event: app name, window title, element role, element title (from the macOS AX API)

```
/learn stop
```

Stops recording and:
1. Saves accessibility replay context immediately to `.openvibe/procedures/<name>.json`
2. Sends the recording (events + screenshots) to the LLM as a multimodal call
3. LLM returns a structured JSON procedure with `task_name`, `description`, `steps`, and `procedure`
4. The LLM output is merged into the procedure file (accessibility context is preserved)

Screenshots are sent as image data directly to the API — they are never embedded as base64 text in the chat log.

## Replaying a task

```
/learn replay "export monthly report"
```

Builds a structured prompt from the recorded accessibility data and starts an autonomous agent turn. The agent:

1. Takes a screenshot to observe the current screen state
2. Opens any applications from the recording that are not currently running
3. Reproduces the recorded interactions in the correct windows
4. Does not ask the user for input — uses tools to figure out anything ambiguous

### Providing runtime context

```
/learn replay "export monthly report" use Q3 data this time
```

Any text after the task name is passed to the agent as additional context. Use this to vary parameters (dates, names, file paths) without re-recording.

## Listing learned tasks

```
/learn list
```

Shows all learned tasks for the current project.

## Procedure files

Procedures are stored per-project in `.openvibe/procedures/<name>.json`:

```json
{
  "task_name": "export monthly report",
  "description": "Export the monthly sales report to PDF from the analytics dashboard",
  "steps": [
    "Open the analytics application",
    "Navigate to the Reports section",
    "Select the monthly report",
    "Export as PDF"
  ],
  "procedure": "Open the analytics application and navigate to the Reports section...",
  "ax_context": {
    "apps": [
      {"name": "Analytics", "windows": ["Dashboard", "Reports"]}
    ],
    "events": [
      {"action": "click", "app": "Analytics", "window": "Reports", "role": "AXButton", "title": "Export"},
      {"action": "key", "key": "return"}
    ]
  }
}
```

The `ax_context` field contains the raw accessibility tree data. This is the primary source for replay — the `procedure` text is a human-readable summary used as a fallback when accessibility data is unavailable.

## How replay works without hardcoding

A common question: how does replay know where to create files or which folder to use, when the recording captured a specific path?

The LLM summarization prompt explicitly instructs the model to:
- Describe **intent**, not implementation (e.g. "create a new document" not "save to `/Users/john/report.docx`")
- Never include absolute file paths in the procedure
- Describe files by purpose ("the output document", "the config file")

The replay prompt is built from accessibility context (which apps were open, which UI elements were interacted with) — not from file paths. The agent then infers the correct location from the current screen state.

## Accessibility context vs screenshots

| | Accessibility context | Screenshots |
|---|---|---|
| Source | macOS AX API (`atomacos`) | `mss` + Pillow |
| Content | App, window, role, element title | Visual image |
| Used for | Replay (WHERE/HOW) | Summarization (WHAT) |
| Privacy | Text only | Visual |
| Reliability | Structural, stable | Layout-dependent |

Accessibility context is the primary source for replay. Screenshots are sent to the LLM during summarization to help it understand what the task achieved visually.

## Recording pipeline

```
pynput listeners (daemon threads)
    │
    ├── on_click  → TrajectoryEvent(click, x, y)
    │                   └── _submit_screenshot(attach_to=event)
    │                   └── _ax_context_at(x, y) [in executor]
    │
    ├── on_scroll → TrajectoryEvent(scroll, x, y, dx, dy)
    │
    └── on_key_press
            ├── modifier (cmd/ctrl/alt/shift) → track in _held_mods
            ├── combo (cmd+s) → TrajectoryEvent(key, "cmd+s")
            ├── special key → TrajectoryEvent(key, "return")
            └── printable → buffer → flush after inactivity → TrajectoryEvent(type, text)
                                                                    └── _ax_focused_context() [in executor]

ThreadPoolExecutor (max 2 workers)
    ├── Screenshot capture (mss → PIL → JPEG → base64)
    └── Accessibility capture (atomacos → dict)
```

Everything runs off the asyncio event loop — pynput listeners use their own daemon threads and the executor handles I/O.
