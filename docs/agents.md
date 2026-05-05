# Agents

An agent is a named configuration that controls how the LLM behaves: which model to use, what system prompt to apply, which tools are available, and what permission rules apply by default.

## Built-in agents

### `build` (default)

Full-access agent for coding and development tasks.

- **System prompt:** Expert AI coding assistant with filesystem, bash, and computer-use tools.
- **Permission rules:** `read`, `glob`, `grep`, `web_fetch`, `todo_*`, `screenshot`, `ui`, `ocr`, `clipboard` → allow. `write`, `edit`, `bash`, `mouse`, `keyboard`, `app` → ask.
- **Use for:** writing code, editing files, running tests, general development.

### `plan`

Read-only agent for code exploration and planning.

- **System prompt:** Analysis agent. Structured output with headings and code blocks.
- **Permission rules:** `read`, `glob`, `grep`, `web_fetch` → allow. `write`, `edit`, `bash`, `todo_write` → deny.
- **Disabled tools:** `bash`, `write`, `edit`, `todo_write`
- **Use for:** understanding a codebase, producing a plan before making changes, answering questions without side effects.

### `general`

General-purpose research subagent.

- **System prompt:** Research agent — gathers information, searches code, fetches web resources.
- **Permission rules:** Read + web fetch only. Write/edit/bash → deny.
- **Disabled tools:** `bash`, `write`, `edit`, `todo_write`
- **Use for:** multi-step web research, background information gathering.

### `computer`

Desktop control agent with an optimised computer-use workflow prompt.

- **System prompt:** Instructs the agent to prefer `ui` over `mouse`, always pass image dimensions, verify actions via screenshot diff.
- **Permission rules:** `screenshot`, `ui`, `ocr`, `clipboard`, `read`, `glob`, `grep` → allow. `mouse`, `keyboard`, `app`, `bash`, `write`, `edit` → ask.
- **Use for:** GUI automation, desktop tasks, anything involving a visible application window.

## Selecting an agent

```python
# Programmatic
session = ov.create_session(agent="plan")

# Via config (sets the project-wide default)
# openvibe.json:
# { "default_agent": "plan" }
```

## Custom agents

Define custom agents in `openvibe.json` under the `agent` key. Custom agents extend the built-in of the same name (if one exists), or start from scratch.

```json
{
  "agent": {
    "reviewer": {
      "description": "Security-focused code review agent.",
      "prompt": "You specialise in security audits. Focus on OWASP Top 10, input validation, and authentication flows. Be concise and actionable.",
      "model": {"provider_id": "anthropic", "model_id": "claude-opus-4-6"},
      "temperature": 0.1,
      "max_steps": 30
    },
    "build": {
      "temperature": 0.2,
      "max_steps": 50
    }
  }
}
```

### Custom agent fields

| Field | Type | Description |
|-------|------|-------------|
| `description` | `str` | Short description shown in `/help` |
| `prompt` | `str` | Additional system prompt appended to the built-in prompt |
| `model` | `{provider_id, model_id}` | Override the model for this agent |
| `temperature` | `float` | Sampling temperature |
| `top_p` | `float` | Top-p sampling |
| `max_steps` | `int` | Hard cap on tool-call iterations per turn |
| `mode` | `"primary" \| "subagent"` | Agent mode (primary = user-facing, subagent = called by another agent) |

`prompt` is appended to the built-in system prompt — it does not replace it. To fully override the system prompt, define an agent name that does not match any built-in.

## Permission rules per agent

Built-in agents have pre-configured permission rules. When you create a custom agent, it inherits the rules of the built-in it extends (if any). You can override them further with project-level `permission` rules in `openvibe.json` — see [Permissions](permissions.md).

## Listing all agents

```python
from openvibe.agent.agent import list_agents
from openvibe.config import load_config

config = load_config(Path("."))
agents = list_agents(config)
for a in agents:
    print(a.name, "-", a.description)
```
