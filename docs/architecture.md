# Architecture

## Overview

```
┌────────────────────────────────────────────────────────┐
│                    Your Application                     │
│      (TUI / web server / script / notebook / CLI)       │
└───────────────────────────┬────────────────────────────┘
                            │
              OpenVibe / Session  (sync public API)
                            │
┌───────────────────────────▼────────────────────────────┐
│                      openvibe core                      │
│                                                         │
│  Session ──► SessionProcessor (async, per-turn loop)   │
│      │              │                                   │
│      │         LLM backend (litellm)                   │
│      │              │                                   │
│      │         ToolRegistry ──► Tool.execute()          │
│      │              │                                   │
│      └──► PermissionService ──► EventBus               │
│                                                         │
│  Config   ◄── openvibe.json / env / global config      │
│  Database ◄── SQLite  (sessions, messages, rules)       │
│  Skills   ◄── bundled + <project>/skills/              │
│  MCP      ◄── McpClientManager (stdio / SSE)           │
└─────────────────────────────────────────────────────────┘
```

## Design principles

**Headless-first.** The public API (`openvibe/api.py`) is synchronous and has no Textual or TUI dependencies. The TUI is a consumer of the API, not the other way around. Any application can embed openvibe with a standard `import`.

**Async inside, sync outside.** The LLM ↔ tool loop runs in a `asyncio` event loop inside a background daemon thread. The public `Session.send()` blocks until the turn completes. No event loop management leaks out to callers.

**Tool isolation.** Each tool is a standalone class. Adding, removing, or replacing a tool requires no changes to the core loop — just register or omit it in the `ToolRegistry`.

**Event bus for decoupling.** The `SessionProcessor` publishes events (`TextDeltaEvent`, `ToolStateChangedEvent`, `PermissionRequestedEvent`, etc.) on an `EventBus`. The TUI subscribes; the headless API polls queues. Neither side needs to know about the other.

## Component reference

| Component | Location | Responsibility |
|-----------|----------|----------------|
| `OpenVibe` | `openvibe/api.py` | Top-level handle. Owns DB, registry, config, MCP. Creates sessions. |
| `Session` | `openvibe/api.py` | One conversation. FSM: IDLE → THINKING → WAITING → IDLE. Launches worker threads. |
| `SessionProcessor` | `openvibe/session/processor.py` | Async LLM ↔ tool loop. Streams tokens, calls tools, publishes events. |
| `ToolRegistry` | `openvibe/tool/base.py` | Dict of `name → Tool`. `create_default_registry()` populates all built-ins. |
| `Tool` | `openvibe/tool/base.py` | Abstract base. Subclasses implement `execute(ctx, params) → ToolResult`. |
| `ToolContext` | `openvibe/tool/base.py` | Runtime context injected into every `execute()` call. |
| `PermissionService` | `openvibe/permission/permission.py` | Evaluates rules. Suspends on `ask`; resumed by `reply()`. |
| `PermissionMode` | `openvibe/permission/permission.py` | `default` / `smart` / `bypass` — session-level rule preset. |
| `EventBus` | `openvibe/bus.py` | Async pub/sub. TUI subscribes for real-time updates. |
| `AgentInfo` | `openvibe/agent/agent.py` | Resolved agent: system prompt, model, permission rules, disabled tools. |
| `Config` | `openvibe/config.py` | Pydantic model. Merged from JSON files + env vars at startup. |
| `SkillDefinition` | `openvibe/skill/base.py` | Abstract base. Implements `get_prompt(args) → str`. |
| `SkillRegistry` | `openvibe/skill/registry.py` | Holds all registered skills. Auto-routes natural-language invocations. |
| `LearnRecorder` | `openvibe/learn/recorder.py` | Global pynput listener + async screenshot + AX context capture. |
| `Trajectory` | `openvibe/learn/trajectory.py` | Recorded event sequence. Builds LLM content blocks for summarization. |
| `Database` | `openvibe/db.py` | SQLite wrapper. Stores sessions, messages, tool calls, permission rules. |
| `McpClientManager` | `openvibe/mcp/client.py` | Connects to MCP servers; registers their tools in the registry. |

## Request lifecycle

```
User calls session.send("fix the tests")
        │
        ▼
Session._try_command()          # slash command? execute locally, return
Session._try_skill()            # /skillname? expand to full prompt
Session._try_auto_route()       # natural language → best matching skill?
        │
        ▼
Session._launch_worker()        # start daemon thread
        │
        ▼  (background thread)
asyncio.new_event_loop()
SessionProcessor.run(text)
        │
        ├── prepend permission rules (mode: smart/bypass)
        ├── build messages (system + history + user)
        │
        ▼
LLM call (litellm stream)
        │
        ├── TextDelta event ──► on_token callback + EventBus
        │
        └── ToolCall requested
                │
                ├── ToolStateChangedEvent (PENDING → RUNNING)
                ├── PermissionService.check()
                │       ├── ALLOW → proceed
                │       ├── ASK → publish PermissionRequestedEvent
                │       │         worker blocks on future
                │       │         caller calls session.reply()
                │       │         future resolves → worker continues
                │       └── DENY → PermissionDenied raised → error result
                │
                ├── tool.execute(ctx, params)
                ├── ToolStateChangedEvent (RUNNING → COMPLETED/ERROR)
                └── result appended to messages
        │
        ▼
LLM call again (with tool result) ... repeat until no more tool calls
        │
        ▼
Response(state=IDLE, text=...) pushed to result_q
        │
        ▼
Session._collect() returns → session.send() returns to caller
```

## Thread model

```
Main thread / caller thread
    │
    └── session.send() blocks on result_q.get()

Worker thread (per turn, daemon)
    │
    └── asyncio.run(SessionProcessor.run())
            │
            ├── LLM streaming (httpx async)
            ├── Tool execution (async coroutines)
            └── Permission suspend:
                    worker blocks on asyncio.Future
                    caller puts (request_id, option) on resume_q
                    PermissionService reads resume_q → resolves future

TUI thread (Textual event loop)
    │
    └── subscribes to EventBus
    └── calls session.send_nowait() / session.reply_nowait()
```

## Database schema

openvibe stores all state in a single SQLite database (`~/.local/share/openvibe/openvibe.db` on Linux/macOS, `%APPDATA%/openvibe/openvibe.db` on Windows).

Key tables:

| Table | Contents |
|-------|----------|
| `projects` | One row per project directory |
| `sessions` | One row per conversation (title, directory, token counts, cost) |
| `messages` | One row per message (role, parts JSON) |
| `permissions` | Stored allow-always rules per project |

## File layout

```
openvibe/
├── api.py                  Public API — OpenVibe, Session, Response
├── config.py               Config schema and loading
├── bus.py                  EventBus
├── db.py                   SQLite wrapper
├── main.py                 CLI entry point (typer)
│
├── agent/
│   └── agent.py            AgentInfo, built-in agents, resolve()
│
├── learn/
│   ├── recorder.py         LearnRecorder — pynput + screenshots + AX
│   ├── trajectory.py       Trajectory, TrajectoryEvent, build_*()
│   └── storage.py          procedure_path(), load_procedure(), list_procedures()
│
├── mcp/
│   └── client.py           McpClientManager
│
├── permission/
│   └── permission.py       PermissionService, Rule, PermissionMode, SMART_MODE_RULES
│
├── session/
│   ├── models.py           SessionInfo, MessageInfo, TextPart, ToolPart, ToolState
│   ├── processor.py        SessionProcessor (async LLM ↔ tool loop)
│   └── session.py          DB helpers — create, get, list, add_message
│
├── skill/
│   ├── base.py             SkillDefinition, SkillValidator, CostTier
│   ├── registry.py         SkillRegistry, register_skill(), get_registry()
│   ├── loader.py           load_skills_dir() — auto-loads <project>/skills/
│   └── bundled/
│       └── __init__.py     init_bundled_skills() — registers built-in skills
│
├── tool/
│   ├── base.py             Tool, ToolContext, ToolResult, ToolRegistry, create_default_registry()
│   ├── bash.py             BashTool
│   ├── read.py             ReadTool
│   ├── write.py            WriteTool
│   ├── edit.py             EditTool
│   ├── glob_tool.py        GlobTool
│   ├── grep_tool.py        GrepTool
│   ├── web_search.py       WebSearchTool
│   ├── web_fetch.py        WebFetchTool
│   ├── browser.py          WebBrowserTool
│   ├── todo.py             TodoReadTool, TodoWriteTool
│   ├── computer_screenshot.py  ScreenshotTool
│   ├── computer_ui.py          UITool
│   ├── computer_mouse.py       MouseTool
│   ├── computer_keyboard.py    KeyboardTool
│   ├── computer_app.py         AppTool
│   ├── computer_clipboard.py   ClipboardTool
│   └── computer_ocr.py         OCRTool
│
└── tui/
    ├── app.py              Textual App subclass
    ├── clipboard.py        copy_to_clipboard(), strip_markup()
    ├── events.py           TUI-internal event types
    ├── screens/
    │   ├── welcome.py      WelcomeScreen
    │   ├── session.py      SessionScreen (main chat UI)
    │   └── sessions.py     SessionListScreen
    └── widgets/
        ├── input_bar.py    InputBar, ChatInput, PermButton
        └── messages.py     MessageList, MessageWidget, ToolWidget
```
