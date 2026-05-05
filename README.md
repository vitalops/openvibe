# openvibe

**An open-source AI coding agent for your terminal — and for embedding headlessly in your own applications.**

```bash
pip install openvibe
vibe
```

---

## Why openvibe?

Most AI coding agents are black boxes: you run a CLI, it does things, you watch. openvibe is different — the terminal interface is just a thin layer on top of a clean Python library you can import directly into any application.

- **Truly headless.** Use `OpenVibe` and `Session` in scripts, web servers, notebooks, or background workers. No subprocess, no server, no event loop to manage.
- **Modular tools.** File editing, web search, desktop control — each is a discrete `Tool` you register or omit. Unused capabilities add zero overhead.
- **Any LLM provider.** Anthropic, OpenAI, Azure, Ollama, Bedrock, Vertex — if [litellm](https://github.com/BerriAI/litellm) supports it, openvibe supports it.
- **Explicit permissions.** Every tool call is checked against an ordered rule list. You control exactly what the agent can do on its own.
- **MCP support.** Connect to any [Model Context Protocol](https://modelcontextprotocol.io) server over stdio or SSE.

---

## Installation

```bash
pip install openvibe

# Learn & replay (record mouse/keyboard + macOS accessibility tree)
pip install "openvibe[learn]"
```

Requires Python 3.11+.

---

## Headless API — embed in your application

```python
from openvibe import OpenVibe
from openvibe.api import SessionState

with OpenVibe(project_dir=Path("/path/to/project")) as ov:
    session = ov.create_session()

    response = session.send(
        "Refactor main.py to use dataclasses",
        on_token=lambda t: print(t, end="", flush=True),
    )

    # Handle permission prompts interactively
    while response.state == SessionState.WAITING:
        req = response.request
        print(f"\n{req.description}")
        choice = input("[allow/deny]: ").strip() or "allow"
        response = session.reply(req.id, choice)

    print(response.text)
```

**One-shot convenience:**

```python
with OpenVibe() as ov:
    result = ov.run("What does this repo do?", on_token=print)
```

**Non-blocking (for GUI/async apps):**

```python
def handle(response):
    if response.state == SessionState.WAITING:
        session.reply_nowait(response.request.id, "allow", callback=handle)
    elif response.state == SessionState.IDLE:
        update_ui(response.text)

session.send_nowait("Fix the failing tests", callback=handle)
```

**Session modes:**

```python
session = ov.create_session()                    # default — ask for every tool
session = ov.create_session(mode="smart")        # pre-approve safe ops
session = ov.create_session(mode="bypass")       # auto-approve everything
```

---

## Terminal UI

```bash
vibe
```

| Key | Action |
|-----|--------|
| `Enter` | Send message |
| `Ctrl+J` | Insert newline |
| `↑` / `↓` | Navigate history |
| `Ctrl+Y` | Copy message / tool output |
| `Ctrl+N` | New session |
| `Ctrl+A` | New session with Smart Permissions |
| `Ctrl+S` | Browse sessions |
| `Ctrl+Q` / `:q` | Quit |

---

## Tools

| Category | Tools |
|----------|-------|
| Filesystem | `read`, `write`, `edit`, `glob`, `grep` |
| Shell | `bash` |
| Web | `web_search`, `web_fetch`, `web_browser` |
| Task tracking | `todo_read`, `todo_write` |
| Desktop | `screenshot`, `ui`, `mouse`, `keyboard`, `app`, `clipboard`, `ocr` |

Computer-use tools are always available alongside coding tools — no special mode required. The `ui` tool uses the macOS Accessibility API for reliable, coordinate-free interaction.

---

## Agents

| Agent | Description |
|-------|-------------|
| `build` | Full-access — coding, file editing, bash (default) |
| `plan` | Read-only — explores code, answers questions |
| `general` | Research subagent — read + web fetch only |
| `computer` | Desktop control — optimised computer-use workflow |

Define custom agents in `openvibe.json`:

```json
{
  "agent": {
    "reviewer": {
      "prompt": "Focus on security and performance.",
      "model": {"provider_id": "anthropic", "model_id": "claude-opus-4-6"}
    }
  }
}
```

---

## Skills

Skills are prompt templates invoked with `/skillname`. They expand to a full LLM prompt without you having to write it.

| Skill | Aliases | Description |
|-------|---------|-------------|
| `/simplify` | — | Review and improve changed code |
| `/debug` | — | Diagnose and fix a bug |
| `/plan` | — | Produce a plan before making changes |
| `/commit` | `/gc` | Generate a commit message and commit |

Write your own in `<project>/skills/my_skill.py` — loaded automatically on startup.

---

## Learn & Replay

Record any computer task once; replay it autonomously later.

```
/learn start "export monthly report"
# ... do the task manually ...
/learn stop
```

openvibe captures mouse clicks, keyboard input, screenshots, and the macOS Accessibility tree at each step. A multimodal LLM call converts the recording into a reusable procedure.

```
/learn replay "export monthly report"
/learn list
```

Replay is fully autonomous — the agent opens required apps, navigates windows, and executes each step without asking for input.

---

## Permissions

Every tool call is checked before it runs.

**Three modes:**

| Mode | Behaviour |
|------|-----------|
| `default` | Ask for every tool call |
| `smart` | Pre-approve safe ops (reads, edits, safe bash, tests) |
| `bypass` | Auto-approve everything |

**Smart Permissions pre-approves:** all file reads, `write`, `edit`, `ls`, `cat`, `find`, `mkdir`, `cp`, `mv`, read-only git, `python`, `pip`, `npm`, and more. Still asks for `rm`, `curl`, `ssh`, `git push`, mouse/keyboard control.

**Per-project rules in `openvibe.json`:**

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

## Configuration

`openvibe.json` (project root or `~/.config/openvibe/openvibe.json` globally):

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
  "permission": [
    {"tool": "bash", "action": "ask"}
  ],
  "instructions": [
    "Always write tests alongside new code."
  ]
}
```

Config resolution order (lowest → highest priority):
1. Built-in defaults
2. `~/.config/openvibe/openvibe.json`
3. `./openvibe.json`
4. Environment variables

Switch model live: `/model anthropic/claude-opus-4-6 --global`

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

## Custom Tools

```python
from pydantic import Field
from openvibe.tool.base import Tool, ToolContext, ToolResult

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

# Register before creating sessions
from openvibe.tool.base import create_default_registry
registry = create_default_registry()
registry.register(SlackTool())

with OpenVibe() as ov:
    ov._registry = registry
    session = ov.create_session()
    session.send("Post a standup update to #general")
```

---

## Multi-Provider Support

| Provider | Example model ID |
|----------|-----------------|
| Anthropic | `claude-sonnet-4-6`, `claude-opus-4-6` |
| OpenAI | `gpt-4o`, `o1` |
| Azure OpenAI | `azure/my-deployment` |
| Google | `gemini/gemini-2.0-flash` |
| Ollama (local) | `ollama/llama3.2`, `ollama/qwen2.5-coder` |
| AWS Bedrock | `bedrock/anthropic.claude-3-5-sonnet` |
| Groq | `groq/llama-3.1-70b-versatile` |

---

## Full Documentation

See [`openvibe/DOCS.md`](openvibe/DOCS.md) for the complete reference covering all API methods, tool parameters, skill authoring, permission rules, config schema, MCP, and architecture.

---

## License

MIT
