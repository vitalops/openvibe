# Headless openvibe

Drive openvibe programmatically from Python — no TUI, no terminal interaction.

---

## When to use this

| Situation | Use headless |
|---|---|
| CI/CD pipeline needs LLM analysis | ✓ |
| Embedding openvibe in another tool | ✓ |
| Background worker processing tasks | ✓ |
| Interactive development in a terminal | Use `openvibe` TUI instead |

---

## Demos

The `run.py` script has four self-contained demos:

### Demo 1 — One-shot headless

The simplest usage: open a context manager, run a prompt, get the full
response back. Tokens stream to a callback in real time.

```python
from openvibe import OpenVibe

with OpenVibe() as ov:
    result = ov.run(
        "What is an LLM agent?",
        on_token=lambda tok: print(tok, end="", flush=True),
    )

print(result.text)
```

### Demo 2 — Multi-turn conversation

Create a `Session` and call `send()` multiple times. Each call blocks until
the agent finishes the turn.

```python
from openvibe import OpenVibe, SessionState

with OpenVibe() as ov:
    session = ov.create_session()

    r1 = session.send("Name three data structures.")
    r2 = session.send("Which is best for FIFO?")
    r3 = session.send("What is its enqueue time complexity?")

    print(r3.text)
```

### Demo 3 — Auto-approve permissions

When the agent calls a tool that needs approval, `send()` returns with
`state=WAITING` and a populated `.request`. Handle it by calling `reply()`.

```python
from openvibe import SessionState

response = session.send("List Python files here.")

while response.state == SessionState.WAITING:
    req = response.request
    print(f"Approving: {req.tool} → {req.argument}")
    response = session.reply(req.id, "allow")  # or "deny"

print(response.text)
```

### Demo 4 — Collect structured output

Ask the model for JSON, then parse it. The example strips markdown fences
in case the model wraps the output.

```python
result = ov.run('Reply with JSON only: {"language": ..., "year": ...}')
data = json.loads(result.text.strip())
```

---

## Credentials

openvibe reads LLM credentials from the same config file the TUI uses:

```
~/.config/openvibe/openvibe.json
```

This file is written automatically when you run `openvibe` in the terminal and
select a model and provider. The headless scripts call `load_config()` at startup —
identical to what `OpenVibe.start()` does internally — so any credentials stored
in that file are applied without needing a separate `export ANTHROPIC_API_KEY=...`.

```python
from openvibe.config import load_config

# Reads ~/.config/openvibe/openvibe.json and pushes api_key values to os.environ.
# Identical to what the TUI does on startup.
load_config()
```

Provider env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.) still work as a
fallback — `load_config()` only sets env vars that are not already present, so
shell-exported keys always take precedence.

---

## Running the demos

```bash
# All four demos
python examples/headless/run.py

# One specific demo
python examples/headless/run.py --demo one-shot
python examples/headless/run.py --demo multi-turn
python examples/headless/run.py --demo permission
python examples/headless/run.py --demo structured
```

---

## API reference

### `OpenVibe`

| Method | Description |
|---|---|
| `OpenVibe()` | Constructor; reads config from `~/.openvibe/config.toml` |
| `ov.run(prompt, on_token=None)` | One-shot: send prompt, block, return `Response` |
| `ov.create_session(agent="general")` | Start a stateful multi-turn session |

Use as a context manager (`with OpenVibe() as ov:`) to ensure clean shutdown.

### `Session`

| Method | Returns | Description |
|---|---|---|
| `session.send(text, on_token=None)` | `Response` | Send a message; blocks until done |
| `session.reply(req_id, choice)` | `Response` | Unblock a WAITING agent |
| `session.send_nowait(text, callback=fn)` | `None` | Fire-and-forget; result via callback |
| `session.reply_nowait(req_id, choice, callback=fn)` | `None` | Non-blocking reply |
| `session.state` | `SessionState` | Current FSM state |
| `session.abort()` | — | Cancel in-flight turn |

### `Response`

```python
response.state          # SessionState: IDLE | WAITING | ERROR
response.text           # full assistant reply (when state=IDLE)
response.request        # InputRequest (when state=WAITING)
response.error          # ErrorInfo (when state=ERROR)
response.command_result # CommandResult if a slash command was run
```

### `SessionState`

```
IDLE     — turn complete; response.text is ready
WAITING  — agent needs input; handle response.request then call reply()
ERROR    — something failed; inspect response.error.message
THINKING — agent is mid-turn (only visible when polling .state in nowait path)
```

---

## Embedding in a larger application

```python
import threading
from openvibe import OpenVibe, SessionState

class AgentWorker:
    def __init__(self):
        self._ov = OpenVibe()
        self._ov.__enter__()
        self._session = self._ov.create_session()

    def ask(self, question: str) -> str:
        response = self._session.send(question)
        # auto-approve any permission requests
        while response.state == SessionState.WAITING:
            response = self._session.reply(response.request.id, "allow")
        if response.state == SessionState.ERROR:
            raise RuntimeError(response.error.message)
        return response.text

    def close(self):
        self._ov.__exit__(None, None, None)
```

---

## Next steps

- See [`../agent_company/`](../agent_company/) for a full evaluation pipeline
  built on top of these primitives.
- Run `openvibe` in the terminal for the interactive TUI.
- Use `/simulate <task description>` inside the TUI to run the simulation
  harness interactively.
