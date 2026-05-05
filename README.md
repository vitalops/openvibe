# openvibe

**An open-source AI coding agent for your terminal — and for embedding headlessly in your own applications.**

```bash
pip install openvibe
vibe
```

---

## Why openvibe?

Most AI coding agents are black boxes: you run a CLI, it does things, you watch. openvibe is different — the terminal UI is just a thin layer on top of a clean Python library you can import directly into any application.

- **Truly headless.** Use `OpenVibe` and `Session` in scripts, web servers, notebooks, or background workers. No subprocess, no server, no event loop to manage.
- **Modular tools.** File editing, web search, desktop control — each is a discrete `Tool` you register or omit. Unused capabilities add zero overhead.
- **Any LLM provider.** Anthropic, OpenAI, Azure, Ollama, Bedrock, Vertex — if [litellm](https://github.com/BerriAI/litellm) supports it, openvibe supports it.
- **Explicit permissions.** Every tool call is checked against an ordered rule list. You control exactly what the agent can do on its own.
- **MCP support.** Connect to any [Model Context Protocol](https://modelcontextprotocol.io) server over stdio or SSE.

---

## Installation

```bash
pip install openvibe

# Desktop control + learn & replay
pip install "openvibe[computer]"
pip install "openvibe[learn]"
```

Requires Python 3.11+.

---

## Headless API

Embed openvibe directly in your application — no CLI, no subprocess.

```python
from pathlib import Path
from openvibe import OpenVibe
from openvibe.api import SessionState

# Point at your project and create a session that auto-approves safe ops
with OpenVibe(project_dir=Path("/path/to/project")) as ov:
    session = ov.create_session(mode="smart")

    # Stream tokens as they arrive
    response = session.send(
        "Find all functions that call the database directly and add "
        "an integration test for each one.",
        on_token=lambda t: print(t, end="", flush=True),
    )

    # The agent may ask for permission before risky actions (e.g. git push)
    while response.state == SessionState.WAITING:
        req = response.request
        print(f"\n\nPermission required: {req.description}")
        choice = input("[allow/deny]: ").strip() or "allow"
        response = session.reply(req.id, choice)

    print(f"\n\nDone. Cost: ${response.cost:.4f}")
```

**Fire and forget — no permission prompts:**

```python
with OpenVibe(project_dir=Path(".")) as ov:
    session = ov.create_session(mode="bypass")
    response = session.send("Write a CHANGELOG entry for everything since the last tag.")
    print(response.text)
```

**Embed in a FastAPI endpoint:**

```python
from fastapi import FastAPI
from openvibe import OpenVibe

app = FastAPI()
ov = OpenVibe(project_dir=Path(".")).start()

@app.post("/review")
async def review(pr_diff: str):
    session = ov.create_session(mode="smart")

    def handle(response):
        ...  # push to websocket, write to DB, etc.

    session.send_nowait(
        f"Review this diff and summarise security concerns:\n\n{pr_diff}",
        callback=handle,
    )
    return {"status": "running"}
```

---

## Permissions

Every tool call is checked before it runs.

| Mode | Behaviour |
|------|-----------|
| `default` | Ask for every tool call |
| `smart` | Pre-approve safe ops (reads, edits, safe bash, running tests) |
| `bypass` | Auto-approve everything |

Smart mode pre-approves: all file reads, `write`, `edit`, `ls`, `cat`, `find`, `mkdir`, `cp`, `mv`, read-only git, `python`, `pip`, `npm`, and more. Still asks for `rm`, `curl`, `ssh`, `git push`, and mouse/keyboard control.

Fine-tune per project in `openvibe.json`:

```json
{
  "permission": [
    {"tool": "read",  "action": "allow"},
    {"tool": "bash",  "action": "deny", "pattern": "rm *"},
    {"tool": "bash",  "action": "ask"}
  ]
}
```

---

## Learn & Replay

Record any computer task once; replay it autonomously later.

```
/learn start "export monthly report"
# ... do the task manually ...
/learn stop
```

openvibe captures mouse clicks, keyboard input, screenshots, and the macOS Accessibility tree at each step. A multimodal LLM converts the recording into a reusable procedure.

```
/learn replay "export monthly report"
/learn list
```

Replay is fully autonomous — the agent opens the required apps, navigates windows, and executes each step without asking for input.

---

## Custom Tools

```python
from pydantic import Field
from openvibe.tool.base import Tool, ToolContext, ToolResult, create_default_registry

class SlackTool(Tool):
    name = "slack"
    description = "Post a message to a Slack channel."

    class Params(Tool.Params):
        channel: str = Field(description="Channel name, e.g. #general")
        message: str = Field(description="Message text.")

    async def execute(self, ctx: ToolContext, params: "SlackTool.Params") -> ToolResult:
        await ctx.check_permission(tool=self.name, argument=params.channel,
                                   description=f"Post to {params.channel}")
        # ... send message ...
        return ToolResult(title=f"Slack → {params.channel}", output="Sent.")

registry = create_default_registry()
registry.register(SlackTool())

with OpenVibe() as ov:
    ov._registry = registry
    session = ov.create_session()
    session.send("Post a standup update to #general")
```

---

## Configuration

`openvibe.json` at project root or `~/.config/openvibe/openvibe.json` globally:

```json
{
  "model": {
    "provider_id": "anthropic",
    "model_id": "claude-sonnet-4-6"
  },
  "provider": {
    "anthropic": {"api_key": "${ANTHROPIC_API_KEY}"}
  },
  "agent": {
    "build": {"temperature": 0.2, "max_steps": 50}
  },
  "instructions": [
    "Always write tests alongside new code."
  ]
}
```

Switch model live: `/model anthropic/claude-opus-4-6 --global`

Supported providers: Anthropic, OpenAI, Azure OpenAI, Google Gemini, Ollama, AWS Bedrock, Groq, Mistral, Together AI, Vertex AI.

---

## MCP

```json
{
  "mcp": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
    },
    "remote": {
      "type": "sse",
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

MCP tools are registered automatically and available to all agents.

---

## Full Documentation

See the [`docs/`](docs/) folder for the complete reference:

| | |
|-|-|
| [Installation](docs/installation.md) | Requirements, extras, first run |
| [API](docs/api.md) | Headless Python API — `OpenVibe`, `Session` |
| [TUI](docs/tui.md) | Terminal interface, key bindings, slash commands |
| [Tools](docs/tools.md) | All built-in tools and parameters |
| [Agents](docs/agents.md) | Built-in and custom agents |
| [Skills](docs/skills.md) | Built-in skills, writing custom skills |
| [Learn & Replay](docs/learn.md) | Record and replay computer tasks |
| [Permissions](docs/permissions.md) | Modes, rules, storage |
| [Configuration](docs/configuration.md) | `openvibe.json` schema |
| [MCP](docs/mcp.md) | Model Context Protocol integration |
| [Custom Tools](docs/custom-tools.md) | Writing your own tools |
| [Providers](docs/providers.md) | Multi-provider LLM support |
| [Architecture](docs/architecture.md) | Component map and data flow |

---

## License

MIT
