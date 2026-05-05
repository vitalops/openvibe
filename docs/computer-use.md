# Computer Use

openvibe can see and control your desktop — taking screenshots, moving the mouse, typing, opening apps, reading the clipboard, and extracting text via OCR. All of this works alongside the standard coding tools in the same session; no special mode is required.

## Installation

```bash
pip install "openvibe[computer]"
```

Installs: `mss` (screen capture), `Pillow` (image processing), `pyautogui` (mouse/keyboard).

On macOS, grant **Accessibility** permission to your terminal app (System Settings → Privacy & Security → Accessibility) before using mouse or keyboard tools.

---

## The `computer` agent

The built-in `computer` agent is tuned for desktop automation tasks. It pre-approves `screenshot` (read-only) and asks for explicit permission before `mouse`, `keyboard`, and `app` actions.

```bash
vibe --agent computer
```

Or headlessly:

```python
with OpenVibe() as ov:
    session = ov.create_session(agent="computer", mode="smart")
    session.send("Open Safari, go to github.com, and screenshot the page.")
```

---

## Tools

### `screenshot`

Captures the screen and sends the PNG directly to the LLM so it can see the current UI state.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `region` | `[x, y, width, height]` | full screen | Capture a sub-region in pixels |
| `zoom` | `[x0, y0, x1, y1]` | — | Crop and return a close-up of a region (logical pixels) |
| `show_cursor` | `bool` | `false` | Overlay a red dot at the current cursor position |
| `marks` | `bool` | `false` | Draw numbered bounding boxes over interactive UI elements (Set-of-Marks) |
| `save_path` | `str` | — | Also write the PNG to this absolute path on disk |

**Set-of-Marks (`marks=true`):** Numbers every button, text field, and link visible in the frontmost window using the platform Accessibility API (AppleScript on macOS, AT-SPI on Linux, UI Automation on Windows). The tool output lists each element's label and coordinates so the LLM can say "click mark 3" instead of guessing pixel positions.

**Retina / HiDPI scaling:** The tool reports `image_width` and `image_height` in its output. Always pass these to the `mouse` tool as `image_width` / `image_height` so coordinates are scaled correctly from screenshot pixels to logical screen pixels.

**Change detection:** On every capture after the first, the tool automatically diffs the new screenshot against the previous one and reports which region changed, what fraction of pixels changed, and a plain-English summary. This lets the LLM confirm that a click or keystroke had the intended effect without an extra round-trip.

```
# Example output
Captured 2560×1600 screenshot (logical screen: 1280×800).
Mouse coordinates: pass image_width=2560, image_height=1600 to the mouse tool.
Change detection vs previous screenshot: 12% change in bottom-right quadrant — new dialog appeared.
```

---

### `mouse`

Controls the mouse pointer. All coordinates are validated against the session sandbox before execution.

| Action | Description |
|--------|-------------|
| `move` | Move pointer to `(x, y)` without clicking |
| `click` | Left-click at `(x, y)` |
| `double_click` | Double left-click at `(x, y)` |
| `triple_click` | Triple left-click — selects a word or line in most editors |
| `right_click` | Right-click at `(x, y)` |
| `middle_click` | Middle-click — opens links in new tab, closes tabs |
| `left_down` | Press and hold the left button at `(x, y)` without releasing |
| `left_up` | Release the left button at `(x, y)` |
| `scroll` | Scroll at `(x, y)`; use `direction` (`up`/`down`/`left`/`right`) and `amount` |
| `drag` | Drag from `(x, y)` to `(end_x, end_y)` |
| `cursor_position` | Return the current cursor location — no coordinates needed |

**Key parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `image_width` / `image_height` | — | Screenshot dimensions — used to translate image pixels to logical screen coordinates on Retina displays. Always provide. |
| `duration` | `0.25s` | Pointer movement duration for smooth animation |
| `settle_ms` | `500ms` | Wait after the action for the UI to settle. Increase to `1000–2000` for slow apps. |

---

### `keyboard`

Simulates keyboard input. Uses clipboard-paste internally for full Unicode support on all platforms (including CJK and emoji).

| Action | Description |
|--------|-------------|
| `type` | Type a string of text. Provide `text`. |
| `press` | Press a single named key. Provide `key` (e.g. `"enter"`, `"escape"`, `"tab"`, `"f5"`). |
| `hotkey` | Send a key combination. Provide `keys` (e.g. `["ctrl", "c"]`, `["cmd", "shift", "4"]`). |
| `hold` | Hold a key for `hold_duration` seconds then release. Useful for shift-click or timed presses. |

**Key names** follow pyautogui conventions (lowercase): `enter`, `escape`, `tab`, `backspace`, `delete`, `up`, `down`, `left`, `right`, `home`, `end`, `pageup`, `pagedown`, `f1`–`f12`, `ctrl`, `alt`, `shift`, `cmd` (macOS) / `win` (Windows).

---

### `app`

Opens, closes, focuses, or lists desktop applications.

| Action | Description |
|--------|-------------|
| `open` | Launch an application by name or path |
| `close` | Quit a running application by name |
| `focus` | Bring a window to the foreground |
| `list` | List all currently running applications / open windows |

Platform support: macOS (AppleScript / `open -a`), Linux (xdg-open / wmctrl / xdotool), Windows (`start` / pygetwindow).

---

### `clipboard`

Reads or writes the system clipboard.

| Action | Description |
|--------|-------------|
| `read` | Return the current clipboard text |
| `write` | Set the clipboard to `text`, then paste with `keyboard hotkey ["ctrl","v"]` |

Platform support: macOS (`pbpaste`/`pbcopy`), Linux (`xclip`/`xsel`/pyperclip), Windows (pyperclip).

---

### `ocr`

Extracts visible text from the screen or a region without using LLM vision tokens. Useful for exact string extraction — error messages, table values, form fields.

| Parameter | Description |
|-----------|-------------|
| `region` | `[x, y, width, height]` — omit to OCR the full screen |

OCR backends tried in order: **pytesseract** → **macOS Vision** (no install needed on macOS) → **Windows WinRT**.

---

## Typical agent loop

The agent's natural rhythm for computer-use tasks:

1. `screenshot` — observe the current screen state
2. `app` — open the required application if not already running
3. `screenshot(marks=true)` — get numbered element references
4. `mouse click` — click a UI element by its mark coordinate
5. `keyboard type` — enter text
6. `screenshot` — verify the action worked (change detection confirms it)
7. Repeat until done

---

## Sandbox constraints

Every session has a `ComputerSandbox` that enforces:

- **Coordinate region** — restrict mouse and screenshot actions to a defined screen area. Useful for multi-monitor setups or kiosk-style deployments.
- **App allow-list** — restrict which applications the agent can open or close.

Configure in code:

```python
from openvibe.computer.sandbox import get_sandbox

sandbox = get_sandbox(session.id)
sandbox.screen_region = (0, 0, 1920, 1080)   # restrict to primary monitor
sandbox.allowed_apps = ["Terminal", "VS Code", "Safari"]
```

---

## Permissions

By default (`mode="default"`) every computer-use action requires explicit approval. In `smart` mode, `screenshot` is pre-approved (read-only); `mouse`, `keyboard`, `app`, and `clipboard` still ask. Use `mode="bypass"` to auto-approve everything.

Add permanent per-project rules in `openvibe.json`:

```json
{
  "permission": [
    {"tool": "screenshot", "action": "allow"},
    {"tool": "mouse",      "action": "ask"},
    {"tool": "keyboard",   "action": "ask"},
    {"tool": "app",        "action": "deny"}
  ]
}
```

---

## Platform notes

| Feature | macOS | Linux | Windows |
|---------|-------|-------|---------|
| Screenshot | `mss` | `mss` | `mss` |
| Mouse / keyboard | pyautogui + Accessibility API | pyautogui + X11 | pyautogui |
| Set-of-Marks | AppleScript / AX API | AT-SPI | UI Automation |
| OCR | macOS Vision (built-in) | pytesseract | WinRT / pytesseract |
| App control | `open -a` / AppleScript | xdg-open / wmctrl | `start` / pygetwindow |
| Clipboard | `pbcopy` / `pbpaste` | xclip / xsel | pyperclip |

Mouse and keyboard control on macOS requires **Accessibility** permission. The tool gives a clear error with instructions if it is not granted.
