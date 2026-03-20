# openvibe — Core Concepts

A guide to the key concepts in openvibe. Read this before touching the code.

---

## Project

A **project** is a directory on disk — usually the root of a git repository.
openvibe detects it automatically by walking up from the current directory until
it finds a `.git` folder. If there is no git repo, the current directory is used.

Projects are stored in the database with a stable ID derived from the absolute
path, so the same directory always maps to the same project regardless of how the
server is started.

All sessions, permissions, and todos belong to a project.

---

## Session

A **session** is one conversation thread within a project. It has a working
directory, an ordered list of messages, and accumulated usage stats (tokens, cost).

Sessions are persistent — they survive server restarts and can be resumed later.
A session can be **forked** from any earlier point, creating a new conversation
that branches off from that message onwards.

---

## Message & Parts

A **message** is one turn in a session. Every message has a role: `user`,
`assistant`, or `system`.

The actual content of a message is stored as an ordered list of **parts**. A single
assistant message can contain multiple parts of different types emitted in sequence
as the model responds:

| Part | What it represents |
|------|--------------------|
| `TextPart` | A chunk of prose the model wrote |
| `ReasoningPart` | Internal thinking (extended thinking / o1-style reasoning) |
| `ToolPart` | A tool call — tracks the full lifecycle from invocation to result |
| `StepStartPart` | A marker at the beginning of each agent iteration |
| `CompactionPart` | A summary that replaced a range of older messages |

Parts are stored as JSON blobs and reconstructed into typed objects on read.

---

## Tool

A **tool** is a capability the agent can invoke — reading a file, running a shell
command, searching code, fetching a URL. The model decides when and how to call
tools based on their name, description, and parameter schema.

Every tool defines a `Params` model (a Pydantic `BaseModel`) that declares its
inputs. This model is automatically converted to a JSON Schema and sent to the LLM
so it knows how to call the tool correctly.

When a tool is called, it receives a `ToolContext` containing the session ID,
working directory, an abort signal, and a reference to the permission service.
It returns a `ToolResult` with a title (shown in the UI) and output text (sent
back to the model).

Built-in tools: `bash`, `read`, `write`, `edit`, `glob`, `grep`, `web_fetch`,
`todo_write`, `todo_read`.

---

## Tool Registry

The **tool registry** is the live collection of all tools available to the agent
during a session. It is populated at server startup with the built-in tools, then
MCP tools are added on top.

Agents can declare a `disabled_tools` list to exclude specific tools from their
registry view without removing them globally.

---

## Agent

An **agent** is a named configuration that controls how the model behaves: which
model to use, what system prompt to show, which tools are available, what
permissions apply, and how many steps it can take before stopping.

Three agents are built in:

| Agent | Mode | Purpose |
|-------|------|---------|
| `build` | primary | Default — full read/write/execute access |
| `plan` | primary | Read-only — analysis and planning only |
| `general` | subagent | Read-only — research and delegated searches |

### Agent Mode

Mode is expressed as a `StrEnum` with two values:

- **`primary`** — the agent drives the conversation and can use all tools it has access to.
- **`subagent`** — read-only, used for delegation. Write and execute tools are disabled.

Custom agents can be defined in `openvibe.json` under the `agent` key. They
override or extend the built-in defaults with the same name.

---

## Permission Rule

A **rule** controls whether the agent is allowed to call a specific tool. Every
tool call is checked against an ordered list of rules before execution. The first
rule that matches wins.

Each rule has three fields:

- **`tool`** — the tool name to match, supports globs (e.g. `file.*`, `*`).
- **`pattern`** — an optional secondary glob matched against the tool's argument (e.g. a file path).
- **`action`** — what to do: `allow`, `deny`, or `ask`.

If no rule matches, the default action is **`ask`** — the agent must get human
approval before proceeding.

Rules can be defined at three levels (each can override the previous):
1. Global config (`~/.config/openvibe/openvibe.json`)
2. Project config (`openvibe.json` in the project root)
3. Agent definition (built-in or custom)

---

## Permission Request

When a rule's action is `ask` (or no rule matches), the tool call **suspends**.
A `PermissionRequestedEvent` is published on the event bus, the HTTP client
receives it as an SSE event, and the user is shown a prompt.

The user responds via `POST /permission/reply`. If they approve, the tool
proceeds. If they deny, the tool call fails with a clear error message returned
to the model.

Approvals can be marked as **remember** — this saves an `allow` rule to the
database so the user is not asked again for that tool on this project.

---

## Event Bus

The **event bus** is the in-process pub/sub system that connects the agent engine
to HTTP clients. Producers (the session processor, the permission service) publish
typed events. Consumers (SSE endpoints) subscribe and forward events to connected
clients in real time.

Every event carries a `session_id` so consumers can filter to the session they
care about. The bus is broadcast-only — all subscribers receive all events and
filter themselves.

---

## LLM Backend

The **LLM backend** is the abstraction over the language model provider. The
default backend uses `litellm`, which supports Anthropic, OpenAI, Google, Bedrock,
Azure, Mistral, and dozens of others through a unified interface.

The backend exposes a single `stream()` method that takes messages and tool
definitions and yields a stream of typed events (`TextDelta`, `ToolCallComplete`,
`StreamDone`, etc.). The rest of the system only ever sees these events — it has
no knowledge of which provider or library is underneath.

To swap the backend, implement the `LLMBackend` protocol and pass it to
`create_app(llm=...)`.

---

## Database

The **database** is where all persistent state lives: projects, sessions,
messages, parts, todos, and permission rules. The default engine is SQLite.

Like the LLM backend, the database is accessed through a `Database` protocol.
All application code calls `execute`, `fetchone`, `fetchall`, etc. — never
touching SQLite directly. This makes it straightforward to swap in a different
engine by implementing the protocol.

---

## Configuration

**Configuration** controls the behaviour of the server: which model to use by
default, how agents are customised, which MCP servers to connect to, and what
permission rules apply project-wide.

Config is loaded from multiple sources in priority order — global user config,
then project config, then environment variables. Later sources win. Dicts are
merged recursively; lists (like `instructions`) are concatenated so both the
global and project configs can contribute entries.

Config files are JSONC (JSON with `//` comments) and support `${ENV_VAR}`
references so API keys are never stored in plain text.

---

## MCP Server

**MCP (Model Context Protocol)** is a standard protocol for exposing tools over
a defined interface. External processes — local or remote — can declare tools
and openvibe will connect to them at startup, discover their tools, and add them
to the tool registry.

MCP servers are configured in `openvibe.json` under the `mcp` key. They can run
as local subprocesses (stdio) or as remote HTTP services (SSE).

---

## Compaction

**Compaction** is how openvibe handles conversations that grow too long to fit in
the model's context window. When the context limit is hit, the oldest portion of
the message history is summarised into a single `CompactionPart`, replacing the
original messages. The most recent messages are kept intact.

The model never sees that compaction happened — from its perspective, the
conversation started with a summary of prior context.

---

## Snapshot

A **snapshot** is a lightweight record of the state of the project's files at a
point in time: the path and SHA-256 hash of every file. Two snapshots can be
diffed to produce a summary of what changed (files added, modified, deleted)
during a session turn.

Snapshots also support **revert** — a full copy of changed files can be saved
to `~/.openvibe/snapshots/` so the workspace can be restored to any prior state.
