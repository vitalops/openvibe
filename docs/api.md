# Headless API

The entire public contract lives in `openvibe.api`. Everything is synchronous and thread-safe. Async internals run inside background worker threads — you never need to manage an event loop.

## OpenVibe

`OpenVibe` is the top-level handle. Create one per process; it manages the database, tool registry, config, and MCP connections.

```python
from pathlib import Path
from openvibe import OpenVibe

# Context manager — handles start/close automatically
with OpenVibe(project_dir=Path("/path/to/project")) as ov:
    session = ov.create_session()
    ...

# Manual lifecycle
ov = OpenVibe()
ov.start()
try:
    ...
finally:
    ov.close()
```

### Constructor parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project_dir` | `Path \| None` | `Path.cwd()` | Project root. Config is loaded from here; relative file paths resolve against it. |
| `config` | `Config \| None` | `None` | Inject a pre-built config (useful for testing). |
| `db` | `Database \| None` | `None` | Inject a database (useful for testing). |
| `llm` | `callable \| None` | `None` | Inject a custom LLM backend (useful for testing). |

### Session management

```python
# Create a session (default mode — asks for every tool)
session = ov.create_session()

# Create with a specific agent
session = ov.create_session(agent="plan")

# Create with Smart Permissions (pre-approves common safe ops)
session = ov.create_session(mode="smart")

# Create with bypass mode (auto-approve everything)
session = ov.create_session(mode="bypass")

# Open an existing session by ID
session = ov.get_session(session_id)

# List recent sessions
sessions = ov.list_sessions()  # list[SessionInfo]

# Archive a session
ov.delete_session(session_id)
```

### One-shot convenience

```python
result = ov.run(
    "What does this codebase do?",
    agent="build",
    on_token=print,
    on_permission="allow",  # "allow" | "deny" | "ask"
)
print(result.text)
```

`on_permission="allow"` auto-approves every tool call. Use it for trusted headless scripts.

---

## Session

`Session` represents a single conversation thread. It has an explicit FSM state:

```
IDLE ──send()──► THINKING ──permission needed──► WAITING
  ▲                  │                               │
  │             turn complete               reply() / reply_nowait()
  └──────────────────┴───────────────────────────────┘
                    ERROR  (recoverable — next send() resets to THINKING)
```

### Blocking API

#### `send(text, on_token=None, on_message=None, on_tool=None) → Response`

Send a message and block until a result is ready.

```python
response = session.send(
    "Refactor main.py to use dataclasses",
    on_token=lambda t: print(t, end="", flush=True),
)
```

Returns when:
- The turn finishes → `Response(state=IDLE)`
- A permission request fires → `Response(state=WAITING)`
- An error occurs → `Response(state=ERROR)`

Slash commands (e.g. `/help`) are intercepted locally and never reach the LLM. Skill invocations (e.g. `/commit`) expand to a full LLM prompt before being sent.

#### `reply(request_id, option) → Response`

Reply to a pending permission request and block for the next result.

```python
while response.state == SessionState.WAITING:
    req = response.request
    print(req.description)
    choice = input("[allow/deny]: ").strip() or "allow"
    response = session.reply(req.id, choice)
```

`option` values:
- `"allow"` or `"1"` — approve this one call
- `"allow_always"` or `"2"` — approve and save as a permanent project rule
- `"deny"` or `"3"` — reject the call

### Non-blocking API

For GUI frameworks or event-driven applications.

#### `send_nowait(text, callback=None, on_token=None, on_message=None, on_tool=None)`

Returns immediately; state transitions to THINKING. Results are delivered via `callback` in a daemon thread.

```python
def handle(response):
    if response.state == SessionState.WAITING:
        session.reply_nowait(response.request.id, "allow", callback=handle)
    elif response.state == SessionState.IDLE:
        print(response.text)
    elif response.state == SessionState.ERROR:
        print("Error:", response.error.message)

session.send_nowait("Fix the failing tests", callback=handle)
```

#### `reply_nowait(request_id, option, callback=None)`

Reply to a pending request and return immediately.

### Callbacks

| Callback | Signature | When called |
|----------|-----------|-------------|
| `on_token` | `(token: str) → None` | Each streaming text token |
| `on_message` | `(msg_id: str, role: str) → None` | When a new message is created |
| `on_tool` | `(msg_id: str, part_index: int, state_dict: dict) → None` | On tool state changes |

### Aborting a turn

```python
session.abort(timeout=5.0)
```

Signals the worker to stop, unblocks any pending permission prompt, and resets state to IDLE.

### Switching model mid-session

```python
session.update_session_config({
    "model": {"provider_id": "anthropic", "model_id": "claude-opus-4-6"}
})
```

---

## Response

Every blocking method returns a `Response`:

```python
@dataclass
class Response:
    state: SessionState      # IDLE | THINKING | WAITING | ERROR
    text: str                # full assistant reply (when IDLE)
    request: InputRequest | None  # permission request (when WAITING)
    error: ErrorInfo | None       # error details (when ERROR)
    command_result: Any           # set when a slash command was executed
```

### InputRequest

```python
@dataclass
class InputRequest:
    id: str
    kind: str           # "permission"
    description: str    # human-readable description of what the tool will do
    tool: str | None    # tool name
    argument: str | None  # the raw value (command, path, …)
    options: list[Option]
```

### SessionState

```python
class SessionState(StrEnum):
    IDLE     = "idle"
    THINKING = "thinking"
    WAITING  = "waiting"
    ERROR    = "error"
```

---

## Session properties

```python
session.id          # str — unique session ID
session.state       # SessionState
session.info        # SessionInfo — title, token counts, cost, directory
session.messages()  # list[MessageInfo] — full conversation history
```
