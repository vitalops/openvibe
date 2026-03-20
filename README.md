# openvibe

Python implementation of [opencode](https://opencode.ai) — an open-source AI coding agent.

## Installation

```bash
pip install -e ".[dev]"
```

## Usage

```bash
# Start the server (default port 4096)
openvibe serve

# One-shot prompt
openvibe run "fix the failing tests"

# List available models
openvibe models

# Session management
openvibe session list
openvibe session show <session-id>
```

## Configuration

Create `openvibe.json` in your project root:

```json
{
  "model": { "provider_id": "anthropic", "model_id": "claude-sonnet-4-5" },
  "default_agent": "build",
  "permission": [
    { "tool": "bash", "action": "ask" },
    { "tool": "write", "action": "ask" }
  ]
}
```

Set your API key:

```bash
export ANTHROPIC_API_KEY=sk-...
```

## Architecture

See [`src/openvibe/`](src/openvibe/) for the source. Key modules:

- `db.py` — SQLite abstraction (swappable via `Database` protocol)
- `llm.py` — LLM abstraction (litellm backend, swappable via `LLMBackend` protocol)
- `server.py` — FastAPI HTTP + SSE server
- `session/processor.py` — core agent execution loop
- `tool/` — built-in tools (bash, read, write, edit, glob, grep, web_fetch, todo)
- `mcp/client.py` — MCP server integration
