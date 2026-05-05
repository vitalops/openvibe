# openvibe — Documentation

> An open-source AI coding agent built for the terminal and for headless integration into your own applications.

---

## Table of Contents

1. [What is openvibe?](#what-is-openvibe)
2. [Installation](#installation)
3. [Quick Start](#quick-start)
4. [Headless API](#headless-api)
   - [Basic Usage](#basic-usage)
   - [Streaming Tokens](#streaming-tokens)
   - [Permission Handling](#permission-handling)
   - [Non-Blocking API](#non-blocking-api)
   - [Session Modes](#session-modes)
   - [Multi-turn Conversations](#multi-turn-conversations)
5. [TUI — Terminal Interface](#tui--terminal-interface)
   - [Launching](#launching)
   - [Key Bindings](#key-bindings)
   - [Session Modes in the TUI](#session-modes-in-the-tui)
   - [Slash Commands](#slash-commands)
6. [Tools](#tools)
   - [Code & Filesystem Tools](#code--filesystem-tools)
   - [Web Tools](#web-tools)
   - [Computer Use Tools](#computer-use-tools)
7. [Agents](#agents)
   - [Built-in Agents](#built-in-agents)
   - [Custom Agents](#custom-agents)
8. [Skills](#skills)
   - [Built-in Skills](#built-in-skills)
   - [Writing a Custom Skill](#writing-a-custom-skill)
9. [Learn & Replay](#learn--replay)
10. [Permissions](#permissions)
    - [Permission Modes](#permission-modes)
    - [Permission Rules](#permission-rules)
    - [Storing Rules Permanently](#storing-rules-permanently)
11. [Configuration](#configuration)
    - [Config File Format](#config-file-format)
    - [Config Resolution Order](#config-resolution-order)
    - [Environment Variables](#environment-variables)
12. [MCP — Model Context Protocol](#mcp--model-context-protocol)
13. [Custom Tools](#custom-tools)
14. [Multi-Provider Support](#multi-provider-support)
15. [Architecture Overview](#architecture-overview)

---

## What is openvibe?

openvibe is an **AI coding agent** that combines a polished terminal UI with a clean Python library you can embed directly in your own application — no server required.

**Key design principles:**

- **Headless-first.** The public API is a plain synchronous Python library. The TUI is built on top of it, not the other way around. You can use openvibe in scripts, web servers, notebooks, or any application without ever launching the terminal interface.
- **Modular tools.** Every capability — file editing, web search, desktop control — is a discrete `Tool` subclass. You register exactly the tools you want; unused capabilities add zero overhead.
- **Any LLM provider.** Backed by [litellm](https://github.com/BerriAI/litellm), openvibe works with Anthropic, OpenAI, Azure OpenAI, Ollama, Bedrock, Vertex, and any other provider litellm supports.
- **Explicit permissions.** Every tool call is checked against an ordered rule list before it runs. You decide what the agent can do autonomously and what requires your approval.
- **MCP support.** Connect to any [Model Context Protocol](https://modelcontextprotocol.io) server over stdio or SSE to give the agent access to external data sources and tools.

---

## Installation

```bash
pip install openvibe
```

**Optional extras:**

```bash
# Learn & replay (record mouse/keyboard + macOS accessibility tree)
pip install "openvibe[learn]"

# All extras
pip install "openvibe[learn]"
```

**Requirements:** Python 3.11+

---

## Quick Start

### One-shot headless run

```python
from openvibe import OpenVibe

with OpenVibe() as ov:
    result = ov.run("What does this repository do?", on_token=print)
    print(result.text)
```

### Interactive headless session

```python
from openvibe import OpenVibe
from openvibe.api import SessionState

with OpenVibe() as ov:
    session = ov.create_session()
    response = session.send("Refactor main.py to use dataclasses")

    # Handle permission prompts
    while response.state == SessionState.WAITING:
        req = response.request
        print(f"\n{req.description}")
        choice = input("[allow/deny]: ").strip() or "allow"
        response = session.reply(req.id, choice)

    print(response.text)
```

### Terminal UI

```bash
vibe          # launch the TUI
# or
openvibe
```

---

## Headless API

The public API lives in `openvibe.api`. Everything is synchronous and thread-safe; async internals are isolated inside background worker threads so you never need to manage an event loop.

### Basic Usage

```python
from openvibe import OpenVibe

# OpenVibe manages the database and tool registry
with OpenVibe(project_dir=Path("/path/to/project")) as ov:
    session = ov.create_session()
    response = session.send("List all Python files over 500 lines")
    print(response.text)
```

`OpenVibe` accepts an optional `project_dir` (defaults to `Path.cwd()`). This determines where `openvibe.json` config is loaded from and where relative file paths are resolved.

### Streaming Tokens

Pass `on_token` to receive each text token as it streams from the model:

```python
response = session.send(
    "Explain this codebase",
    on_token=lambda t: print(t, end="", flush=True),
)
```

### Permission Handling

When the agent wants to run a tool that requires approval, `send()` returns a `Response` with `state=WAITING`:

```python
from openvibe.api import SessionState

response = session.send("Delete all .pyc files")

while response.state == SessionState.WAITING:
    req = response.request
    print(f"Tool: {req.tool}")
    print(f"Action: {req.description}")
    print(f"Options: {[o.label for o in req.options]}")

    choice = input("Choice: ").strip()
    response = session.reply(req.id, choice)
```

**Option values:**
- `"allow"` / `"1"` — approve this one call
- `"allow_always"` / `"2"` — approve and remember permanently for this project
- `"deny"` / `"3"` — reject the call

### Non-Blocking API

For GUI frameworks or event-driven applications, use `send_nowait` and `reply_nowait`. Results are delivered via a callback in a daemon thread:

```python
def handle_response(response):
    if response.state == SessionState.WAITING:
        # Auto-approve for headless use
        session.reply_nowait(response.request.id, "allow", callback=handle_response)
    elif response.state == SessionState.IDLE:
        print(response.text)
    elif response.state == SessionState.ERROR:
        print("Error:", response.error.message)

session.send_nowait(
    "Fix the failing tests",
    callback=handle_response,
    on_token=lambda t: print(t, end="", flush=True),
)
```

### Session Modes

Control how permissions are handled for the entire session lifetime:

```python
# Default — ask for every tool call (safest)
session = ov.create_session()

# Smart Permissions — pre-approves common safe operations
# (file reads/edits, safe bash commands, running tests)
# Still asks for anything potentially destructive
session = ov.create_session(mode="smart")

# Bypass — auto-approves everything (use with trusted tasks only)
session = ov.create_session(mode="bypass")
```

See [Permission Modes](#permission-modes) for the full list of what Smart Permissions pre-approves.

### Multi-turn Conversations

A session maintains conversation history automatically. Just call `send()` again:

```python
with OpenVibe() as ov:
    session = ov.create_session()
    session.send("Read main.py and summarise it")
    session.send("Now add type annotations to every function")
    session.send("Run the tests and fix any failures")
```

### One-shot convenience

```python
with OpenVibe() as ov:
    # Creates a session, runs the prompt, auto-approves all permissions, returns
    result = ov.run(
        "What is the test coverage?",
        on_permission="allow",  # "allow" | "deny" | "ask"
    )
```

---

## TUI — Terminal Interface

### Launching

```bash
vibe                   # start in current directory
vibe /path/to/project  # start in a specific directory
```

### Key Bindings

| Key | Action |
|-----|--------|
| `Enter` | Send message |
| `Ctrl+J` | Insert newline |
| `↑` / `↓` | Navigate input history |
| `Ctrl+Y` | Copy last assistant message (or focused widget) |
| `Ctrl+N` | New session |
| `Ctrl+A` | New session with Smart Permissions |
| `Ctrl+S` | Browse all sessions |
| `Ctrl+Q` or `:q` | Quit |
| `Escape` | Cancel current agent turn |

**Copying content:** Click any message or tool output to focus it, then `Ctrl+Y` to copy its text to the clipboard. Falls back to the last assistant message if nothing is focused.

### Session Modes in the TUI

From the welcome screen:

- **New Session** — standard session; the agent asks before running each tool.
- **Smart Permissions** (`Ctrl+A`) — common safe operations are pre-approved; the agent works without interruption on routine tasks.

When a permission prompt appears, the input bar shows three buttons:

| Button | Meaning |
|--------|---------|
| `1 allow` | Approve this one call |
| `2 always` | Approve and remember permanently |
| `3 deny` | Reject the call |

Press the number key or click the button. `Enter` defaults to allow.

### Slash Commands

Type these in the chat input:

| Command | Description |
|---------|-------------|
| `/help` | List all commands and skills |
| `/skills` | List skills with full metadata |
| `/clear` | Clear the chat display |
| `/cost` | Show token usage and estimated cost |
| `/model` | Show or switch the active model |
| `/model anthropic/claude-opus-4-6` | Switch model for this session |
| `/model anthropic/claude-opus-4-6 --global` | Switch model globally |
| `/permissions` | Show active permission rules |
| `/permissions reset` | Clear all stored allow-always rules |
| `/config` | Show current configuration |
| `/init` | Create `openvibe.json` in the project directory |
| `/screenshot` | Show screen dimensions |
| `/computer` | Show computer-use audit log |
| `/learn start <name>` | Start recording a task |
| `/learn stop` | Stop recording and generate procedure |
| `/learn replay <name>` | Replay a learned task |
| `/learn list` | List all learned tasks |
| `/quit` or `:q` | Exit |

---

## Tools

All tools are registered in the default registry. Each tool exposes a Pydantic `Params` schema that is automatically converted to JSON Schema and sent to the LLM.

### Code & Filesystem Tools

| Tool | Description |
|------|-------------|
| `read` | Read a file. Supports line ranges. |
| `write` | Create or overwrite a file (full content). Requires absolute or explicit relative path. |
| `edit` | Targeted string replacement in an existing file. Each `old_string` must appear exactly once. |
| `glob` | Find files matching a pattern (e.g. `**/*.py`). Returns paths sorted by modification time. |
| `grep` | Search file contents with regex. Supports context lines, file-type filters. |
| `bash` | Execute a shell command. Supports timeout (1–600s). Runs in the project directory. |
| `todo_read` | Read the current session todo list. |
| `todo_write` | Write/update the session todo list. |

**Important:** `write` and `edit` reject bare filenames (e.g. `output.txt`). The agent must provide an absolute path or an explicit relative path (`./output.txt`). This prevents files from being silently created in the wrong directory.

### Web Tools

| Tool | Description |
|------|-------------|
| `web_search` | Search the web (DuckDuckGo). Returns titles, URLs, snippets. |
| `web_fetch` | Fetch a URL and return readable text (strips HTML). |
| `web_browser` | Full Selenium browser session for JavaScript-heavy pages. |

### Computer Use Tools

Computer use tools let the agent see and control the desktop. They work automatically alongside all other tools — no special mode is required.

| Tool | Description |
|------|-------------|
| `screenshot` | Capture the screen. Supports `marks` (Set-of-Marks numbered overlays), `zoom` (crop region), `show_cursor`. |
| `ui` | macOS Accessibility API control. `get_tree`, `click`, `click_menu`, `type`, `press_key`. Preferred over raw mouse. |
| `mouse` | Raw mouse control: `click`, `right_click`, `middle_click`, `triple_click`, `move`, `scroll`, `drag`, `left_down`, `left_up`, `cursor_position`. |
| `keyboard` | Raw keyboard input: `type`, `press`, `hold`. |
| `app` | Open, close, focus, list applications. |
| `clipboard` | Read and write the system clipboard. |
| `ocr` | Extract text from the screen or a region using OCR. |

**Computer use tool priority (recommended order):**
1. `ui` — accessibility-based, no coordinates, most reliable
2. `app` — open/focus applications
3. `screenshot` — observe screen state
4. `mouse` — raw coordinates, last resort
5. `keyboard` — raw keystroke fallback

---

## Agents

An agent is a named configuration: system prompt, model, permission rules, and optional tool restrictions.

### Built-in Agents

| Agent | Mode | Description |
|-------|------|-------------|
| `build` | primary | Full-access agent for coding and development. Asks before write/edit/bash. |
| `plan` | primary | Read-only. Can explore files and search but cannot modify anything. |
| `general` | subagent | Research subagent. Read + web fetch only. |
| `computer` | primary | Desktop control agent. Optimised prompt for computer-use workflow. |

### Custom Agents

Define custom agents in `openvibe.json`:

```json
{
  "agent": {
    "reviewer": {
      "description": "Code review agent",
      "prompt": "Focus on security, performance, and maintainability. Be concise.",
      "model": {"provider_id": "anthropic", "model_id": "claude-opus-4-6"},
      "temperature": 0.1,
      "max_steps": 20
    }
  }
}
```

Use a custom agent programmatically:

```python
session = ov.create_session(agent="reviewer")
```

---

## Skills

Skills are named prompt templates that route through the LLM (unlike slash commands which execute locally). They expand a short `/skillname` invocation into a detailed LLM prompt.

### Built-in Skills

| Skill | Aliases | Description |
|-------|---------|-------------|
| `/simplify` | — | Review changed code for reuse, quality, and efficiency, then fix issues found. |
| `/debug` | — | Diagnose and fix a bug or error. |
| `/plan` | — | Produce a structured plan before making changes. |
| `/commit` | `/gc` | Generate a conventional commit message and commit staged changes. |

Skills appear in `/help` and `/skills`. They support auto-routing: if you describe what you want in natural language (e.g. "the tests are failing after my refactor"), openvibe may automatically invoke `/debug` without the `/` prefix.

### Writing a Custom Skill

Create a file in `<project>/skills/my_skill.py`:

```python
from openvibe.skill.base import SkillDefinition, CostTier
from openvibe.skill import register_skill

class SecurityReviewSkill(SkillDefinition):
    name = "security"
    description = "Run a security review of the changed files."
    aliases = ["sec"]
    tags = ["security", "vulnerability", "audit"]
    capabilities = ["code_review", "security_analysis"]
    cost_estimate = CostTier.MEDIUM
    when_to_use = "After making changes that touch auth, input handling, or data storage."
    argument_hint = "[focus area]"

    def get_prompt(self, args: str) -> str:
        focus = f" Focus on: {args}." if args else ""
        return (
            f"Perform a security review of the recently changed files.{focus}\n\n"
            "Check for: SQL injection, XSS, authentication bypasses, "
            "insecure deserialization, path traversal, hardcoded secrets, "
            "and any other OWASP Top 10 issues.\n\n"
            "For each finding: describe the vulnerability, its severity "
            "(Critical/High/Medium/Low), and provide a concrete fix."
        )

register_skill(SecurityReviewSkill())
```

Skills in `<project>/skills/` are loaded automatically on startup.

---

## Learn & Replay

Learn lets you record a computer task once and replay it autonomously.

### Recording

```
/learn start "export monthly report"
```

While recording, openvibe captures globally:
- Every mouse click and scroll
- Every keyboard keystroke (buffered into type events)
- Screenshots after significant actions
- macOS Accessibility tree context per event (app name, window title, element role/title)

```
/learn stop
```

On stop, the recording is analysed by a multimodal LLM call (images sent directly — never embedded in chat). The result is saved as a JSON procedure file in `.openvibe/procedures/<name>.json`.

### Replaying

```
/learn replay "export monthly report"
/learn replay "export monthly report" use Q3 data this time
```

Replay builds a structured prompt from the recorded accessibility context (apps, windows, UI element interactions) and starts an autonomous agent turn. The agent:
1. Takes a screenshot to see the current state
2. Opens any required applications that are not already running
3. Reproduces the interactions in the correct windows
4. Does not ask for user input — it uses tools to figure everything out

Procedures are per-project and stored in `.openvibe/procedures/`.

```
/learn list    # show all learned tasks for this project
```

**Dependencies for Learn:**
```bash
pip install "openvibe[learn]"
# macOS accessibility tree capture requires atomacos
```

---

## Permissions

Every tool call is checked against an ordered list of rules before execution. The first matching rule wins.

### Permission Modes

Set at session creation time:

| Mode | Behaviour |
|------|-----------|
| `"default"` | Ask the user for every tool call. |
| `"smart"` | Pre-approve common safe operations; ask for anything potentially destructive. |
| `"bypass"` | Auto-approve everything. Use only for fully trusted tasks. |

**Smart Permissions pre-approves:**
- All read tools: `read`, `glob`, `grep`, `screenshot`, `ocr`, `clipboard`
- File editing: `write`, `edit`
- Safe bash: `ls`, `cat`, `head`, `tail`, `find`, `wc`, `diff`, `echo`, `pwd`, `mkdir`, `touch`, `cp`, `mv`
- Read-only git: `git status`, `git log`, `git diff`, `git show`, `git branch`
- Running code: `python`, `pip`, `uv`, `npm`, `node`, `cargo`, `go`

**Smart Permissions still asks for:** `rm`, `curl`, `wget`, `ssh`, `git push`, `git commit`, arbitrary scripts, mouse control, keyboard control, app launches.

```python
# Programmatic
session = ov.create_session(mode="smart")

# TUI: Ctrl+A or click "Smart Permissions" on the welcome screen
```

### Permission Rules

Rules use [fnmatch](https://docs.python.org/3/library/fnmatch.html) glob patterns.

**Actions:**
- `allow` — proceed immediately, no prompt
- `ask` — suspend and prompt the user
- `deny` — raise `PermissionDenied` and abort the tool call

**In config:**
```json
{
  "permission": [
    {"tool": "read",  "action": "allow"},
    {"tool": "bash",  "action": "ask"},
    {"tool": "bash",  "action": "deny", "pattern": "rm *"},
    {"tool": "write", "action": "allow", "pattern": "/tmp/*"}
  ]
}
```

Rules are evaluated in order. The first matching rule wins. Deny rules fire regardless of position because they are checked before allow — to block a specific command, place its deny rule before any broader allow rule.

**Rule evaluation order:**
1. Session-mode rules (smart/bypass prepend rules)
2. Agent-level rules (defined per agent)
3. Project config rules (`openvibe.json`)
4. Stored "allow always" rules (saved from interactive prompts)
5. Default: ask

### Storing Rules Permanently

When a permission prompt appears, choosing `"2 always"` saves the rule to the project database. These rules persist across sessions and can be inspected with:

```
/permissions        # list all rules
/permissions reset  # clear all stored rules
```

---

## Configuration

### Config File Format

`openvibe.json` (or `openvibe.jsonc` for comments, or `.openvibe/openvibe.json`):

```json
{
  "model": {
    "provider_id": "anthropic",
    "model_id": "claude-sonnet-4-6"
  },
  "provider": {
    "anthropic": {
      "api_key": "${ANTHROPIC_API_KEY}"
    },
    "openai": {
      "api_key": "${OPENAI_API_KEY}"
    }
  },
  "agent": {
    "build": {
      "temperature": 0.2,
      "max_steps": 50
    },
    "myagent": {
      "description": "Custom agent",
      "prompt": "You specialise in data pipelines.",
      "model": {"provider_id": "openai", "model_id": "gpt-4o"}
    }
  },
  "permission": [
    {"tool": "read",  "action": "allow"},
    {"tool": "bash",  "action": "deny", "pattern": "rm *"},
    {"tool": "bash",  "action": "ask"}
  ],
  "instructions": [
    "Always write tests alongside new code.",
    "Prefer async/await over callbacks."
  ],
  "default_agent": "build",
  "mcp": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/docs"]
    }
  }
}
```

### Config Resolution Order

Config sources are merged in priority order (lowest → highest):

1. **Built-in defaults** — hardcoded in `openvibe/config.py`
2. **Global user config** — `~/.config/openvibe/openvibe.json`
3. **Project config** — `./openvibe.json`, `./openvibe.jsonc`, or `./.openvibe/openvibe.json`
4. **Environment variables** — `OPENVIBE_*` prefixed

Dicts are deep-merged. Lists (e.g. `instructions`) are concatenated.

### Environment Variables

`${VAR}` references in config values are expanded from the environment:

```json
{
  "provider": {
    "anthropic": {"api_key": "${ANTHROPIC_API_KEY}"}
  }
}
```

Standard env vars also work directly (via litellm):
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `AZURE_API_KEY`, `AZURE_API_BASE`, `AZURE_API_VERSION`

Switch model from the TUI:
```
/model anthropic/claude-opus-4-6          # this session
/model openai/gpt-4o --project            # this project
/model ollama/llama3.2 --global           # globally
```

---

## MCP — Model Context Protocol

Connect to any MCP server to give the agent access to external tools and data:

```json
{
  "mcp": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
    },
    "github": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"}
    },
    "remote-server": {
      "type": "sse",
      "url": "http://localhost:8080/mcp",
      "headers": {"Authorization": "Bearer ${MCP_TOKEN}"}
    }
  }
}
```

MCP tools are registered automatically at startup alongside built-in tools.

---

## Custom Tools

Subclass `Tool` and register it on the registry:

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
        # Your implementation here
        await ctx.check_permission(
            tool=self.name,
            argument=params.channel,
            description=f"Post to {params.channel}",
        )
        # ... post to Slack ...
        return ToolResult(
            title=f"Slack → {params.channel}",
            output=f"Message sent to {params.channel}.",
        )
```

Register before creating sessions:

```python
from openvibe import OpenVibe
from openvibe.tool.base import create_default_registry

registry = create_default_registry()
registry.register(SlackTool())

with OpenVibe() as ov:
    # Inject the custom registry
    ov._registry = registry
    session = ov.create_session()
    session.send("Post a standup update to #general")
```

**ToolContext** provides:
- `ctx.session_id`, `ctx.project_id`, `ctx.working_dir`
- `ctx.check_permission(tool, argument, description)` — raises `PermissionDenied` or `PermissionRejected` if not allowed
- `ctx.abort` — asyncio Event, check this in long-running tools

**ToolResult** fields:
- `title` — short label shown in the TUI
- `output` — text returned to the LLM (auto-truncated at 4,000 chars)
- `error` — set `True` to indicate failure
- `metadata` — arbitrary dict (set `{"truncated": True}` to disable auto-truncation)

---

## Multi-Provider Support

openvibe uses [litellm](https://github.com/BerriAI/litellm) as its LLM backend, supporting 100+ providers:

| Provider | Example model_id |
|----------|-----------------|
| Anthropic | `claude-sonnet-4-6`, `claude-opus-4-6` |
| OpenAI | `gpt-4o`, `gpt-4o-mini`, `o1` |
| Azure OpenAI | `azure/my-deployment` |
| Google | `gemini/gemini-2.0-flash` |
| Ollama (local) | `ollama/llama3.2`, `ollama/qwen2.5-coder` |
| AWS Bedrock | `bedrock/anthropic.claude-3-5-sonnet` |
| Groq | `groq/llama-3.1-70b-versatile` |

Switch providers at any level: global config, project config, per-agent config, or live with `/model`.

---

## Architecture Overview

```
┌────────────────────────────────────────────────┐
│                  Your Application               │
│    (TUI / web server / script / notebook)       │
└──────────────────────┬─────────────────────────┘
                       │ OpenVibe / Session (sync API)
┌──────────────────────▼─────────────────────────┐
│                   openvibe core                 │
│                                                 │
│  Session ──► SessionProcessor (async)           │
│      │              │                           │
│      │         LLM backend (litellm)            │
│      │              │                           │
│      │         ToolRegistry ──► Tool.execute()  │
│      │              │                           │
│      └──► PermissionService ──► EventBus        │
│                                                 │
│  Config ◄── openvibe.json / env / global cfg   │
│  Database ◄── SQLite (sessions, messages, rules)│
│  Skills ◄── bundled + project skills/           │
│  MCP ◄── McpClientManager (stdio / SSE)         │
└─────────────────────────────────────────────────┘
```

**Key components:**

| Component | Location | Role |
|-----------|----------|------|
| `OpenVibe` | `openvibe/api.py` | Top-level handle. Manages DB, registry, MCP, config. |
| `Session` | `openvibe/api.py` | One conversation thread. FSM: IDLE → THINKING → WAITING → IDLE. |
| `SessionProcessor` | `openvibe/session/processor.py` | Async LLM ↔ tool loop. Publishes events on the bus. |
| `ToolRegistry` | `openvibe/tool/base.py` | Holds all registered tools. `create_default_registry()` loads all built-ins. |
| `PermissionService` | `openvibe/permission/permission.py` | Evaluates rules; suspends on `ask` until `reply()` is called. |
| `EventBus` | `openvibe/bus.py` | Async pub/sub; TUI subscribes for real-time updates. |
| `AgentInfo` | `openvibe/agent/agent.py` | Resolved agent config: system prompt, model, rules, disabled tools. |
| `Config` | `openvibe/config.py` | Pydantic model. Loaded from JSON files + env vars. |
| `SkillDefinition` | `openvibe/skill/base.py` | Abstract base for skills. `get_prompt(args)` returns the LLM prompt. |
| `LearnRecorder` | `openvibe/learn/recorder.py` | Global pynput listener + async screenshot + accessibility capture. |
