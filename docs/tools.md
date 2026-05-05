# Tools

Every tool is a `Tool` subclass registered in the `ToolRegistry`. The default registry (created by `create_default_registry()`) includes all built-in tools listed here.

Tools expose a Pydantic `Params` schema that is automatically converted to JSON Schema and sent to the LLM so it knows how to call each tool.

---

## Filesystem tools

### `read`

Read a file from the filesystem.

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | `str` | Absolute or project-relative file path |
| `offset` | `int \| None` | Line number to start reading from |
| `limit` | `int \| None` | Maximum number of lines to read |

Output is returned in `cat -n` format (with line numbers).

---

### `write`

Create or overwrite a file with given content. Creates parent directories automatically.

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | `str` | Absolute or project-relative path to write |
| `content` | `str` | Full file content |

**Note:** Bare filenames (e.g. `output.txt`) are rejected. The agent must provide an absolute path or an explicit relative path starting with `./` or `../`. This prevents files from being silently created in the wrong directory.

---

### `edit`

Targeted string replacement in an existing file. More efficient than `write` for modifications — only sends the diff.

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | `str` | Absolute or project-relative file path |
| `edits` | `list[Edit]` | One or more `{old_string, new_string}` replacements applied in order |

Each `old_string` must appear **exactly once** in the file — if it matches zero or more than one location, the edit is rejected. Use more surrounding context to make the match unique.

---

### `glob`

Find files matching a glob pattern, sorted by modification time (most recent first).

| Parameter | Type | Description |
|-----------|------|-------------|
| `pattern` | `str` | Glob pattern, e.g. `**/*.py`, `src/**/*.ts` |
| `path` | `str \| None` | Directory to search in (defaults to working directory) |

---

### `grep`

Search file contents with a regex pattern.

| Parameter | Type | Description |
|-----------|------|-------------|
| `pattern` | `str` | Regular expression to search for |
| `path` | `str \| None` | File or directory to search |
| `glob` | `str \| None` | File glob filter, e.g. `*.py` |
| `-A` | `int \| None` | Lines to show after each match |
| `-B` | `int \| None` | Lines to show before each match |
| `-C` | `int \| None` | Lines to show before and after each match |

---

## Shell tools

### `bash`

Execute a shell command in the project's working directory.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `command` | `str` | — | The bash command to run |
| `timeout` | `int` | `120` | Timeout in seconds (1–600) |
| `description` | `str` | `""` | Human-readable description shown in permission prompts |

stdout and stderr are combined in the output. Exit code non-zero sets `error=True` on the result.

---

## Task tracking tools

### `todo_read`

Read the current session's todo list.

No parameters.

### `todo_write`

Write or replace the session todo list.

| Parameter | Type | Description |
|-----------|------|-------------|
| `todos` | `list[TodoItem]` | List of `{content, status, priority, id}` items |

`status`: `"pending"` | `"in_progress"` | `"completed"`
`priority`: `"high"` | `"medium"` | `"low"`

---

## Web tools

### `web_search`

Search the web using DuckDuckGo.

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | `str` | Search query |
| `max_results` | `int` | Maximum results to return (default 10) |

Returns titles, URLs, and snippets.

### `web_fetch`

Fetch a URL and return readable plain text (HTML is stripped).

| Parameter | Type | Description |
|-----------|------|-------------|
| `url` | `str` | URL to fetch |
| `max_length` | `int \| None` | Maximum characters to return |

### `web_browser`

Full Selenium-based browser session for JavaScript-heavy pages.

| Parameter | Type | Description |
|-----------|------|-------------|
| `url` | `str` | URL to open |
| `action` | `str` | `"get"` \| `"click"` \| `"type"` \| `"screenshot"` \| `"source"` |
| `selector` | `str \| None` | CSS selector for click/type actions |
| `text` | `str \| None` | Text to type |

---

## Computer use tools

Computer use tools let the agent see and control the desktop. They are always available alongside coding tools — no special mode is required.

### `screenshot`

Capture the screen and return a base64 JPEG image.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `marks` | `bool` | `false` | Overlay Set-of-Marks numbered boxes on accessible elements |
| `show_cursor` | `bool` | `false` | Overlay a dot at the current cursor position |
| `zoom` | `[x0,y0,x1,y1] \| None` | `null` | Crop to this screen region before returning |

Output includes image dimensions. Always note these before using `mouse` coordinates — Retina displays require the image dimensions for correct coordinate scaling.

---

### `ui`

macOS Accessibility API control. Preferred over raw `mouse` — no coordinates needed.

| Action | Parameters | Description |
|--------|-----------|-------------|
| `get_tree` | `app` | List all accessible elements in an app |
| `click` | `app`, `title` | Click an element by its label |
| `click_menu` | `app`, `path` | Trigger a menu item, e.g. `["File", "Save"]` |
| `type` | `app`, `title`, `text` | Type text into an element |
| `press_key` | `app`, `key` | Press a key or chord, e.g. `"cmd+s"`, `"return"` |
| `focus` | `app` | Bring an app to the foreground |

`ui` is auto-allowed — no permission prompt.

---

### `mouse`

Raw mouse control. Use as a last resort when `ui` cannot find the target element.

| Action | Parameters | Description |
|--------|-----------|-------------|
| `click` | `x`, `y`, `image_width`, `image_height` | Left click at coordinates |
| `right_click` | `x`, `y`, `image_width`, `image_height` | Right click |
| `middle_click` | `x`, `y`, `image_width`, `image_height` | Middle click |
| `triple_click` | `x`, `y`, `image_width`, `image_height` | Triple click (select word) |
| `move` | `x`, `y`, `image_width`, `image_height` | Move cursor |
| `scroll` | `x`, `y`, `direction`, `amount` | Scroll `up`/`down`/`left`/`right` |
| `drag` | `start_x`, `start_y`, `end_x`, `end_y`, `image_width`, `image_height` | Click and drag |
| `left_down` | `x`, `y`, `image_width`, `image_height` | Press and hold left button |
| `left_up` | `x`, `y`, `image_width`, `image_height` | Release left button |
| `cursor_position` | — | Return current cursor coordinates |

**Always** pass `image_width` and `image_height` from the preceding `screenshot` output. Without them, Retina scaling causes incorrect coordinates.

---

### `keyboard`

Raw keyboard input.

| Action | Parameters | Description |
|--------|-----------|-------------|
| `type` | `text` | Type a string |
| `press` | `key` | Press a key or chord, e.g. `"cmd+s"`, `"escape"` |
| `hold` | `key`, `hold_duration` | Hold a key for N seconds |

---

### `app`

Open, close, focus, and list applications.

| Action | Parameters | Description |
|--------|-----------|-------------|
| `open` | `name` | Open an application by name |
| `close` | `name` | Close an application |
| `focus` | `name` | Bring an application to the foreground |
| `list` | — | List all running applications |

---

### `clipboard`

Read and write the system clipboard.

| Action | Parameters | Description |
|--------|-----------|-------------|
| `read` | — | Return current clipboard text |
| `write` | `text` | Write text to the clipboard |

Works on macOS (`pbcopy`/`pbpaste`), Linux (`xclip`/`xsel`/`wl-copy`), and cross-platform via `pyperclip`.

---

### `ocr`

Extract text from the screen using OCR.

| Parameter | Type | Description |
|-----------|------|-------------|
| `region` | `[x0,y0,x1,y1] \| None` | Screen region to OCR (full screen if omitted) |

Uses pytesseract → macOS Vision (Swift) → Windows WinRT, whichever is available.

---

## Computer use tool priority

Always follow this order — earlier options are more reliable:

1. **`ui`** — accessibility-based, no coordinates, most reliable
2. **`app`** — open/focus applications
3. **`screenshot`** — observe screen state and get dimensions
4. **`mouse`** — raw coordinates, last resort for unlabelled canvas areas
5. **`keyboard`** — raw keystroke fallback
