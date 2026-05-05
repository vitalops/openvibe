# Configuration

openvibe uses a layered JSON config system. All settings are optional — sensible defaults are built in.

## Config file locations

| Priority | Location |
|----------|----------|
| Highest | Environment variables |
| ↑ | `./openvibe.json` or `./openvibe.jsonc` |
| ↑ | `./.openvibe/openvibe.json` |
| ↑ | `~/.config/openvibe/openvibe.json` (global user config) |
| Lowest | Built-in defaults |

Later sources deep-merge into earlier ones. Dicts are merged recursively; lists (e.g. `instructions`) are concatenated.

## Full schema

```json
{
  "model": {
    "provider_id": "anthropic",
    "model_id": "claude-sonnet-4-6"
  },

  "provider": {
    "anthropic": {
      "api_key": "${ANTHROPIC_API_KEY}",
      "base_url": null,
      "api_version": null
    },
    "openai": {
      "api_key": "${OPENAI_API_KEY}"
    },
    "azure": {
      "api_key": "${AZURE_API_KEY}",
      "base_url": "https://my-instance.openai.azure.com",
      "api_version": "2024-02-01"
    }
  },

  "agent": {
    "build": {
      "temperature": 0.2,
      "max_steps": 50
    },
    "myagent": {
      "description": "Custom agent for data pipelines.",
      "prompt": "You specialise in ETL and data engineering.",
      "model": {"provider_id": "openai", "model_id": "gpt-4o"},
      "temperature": 0.3,
      "top_p": 0.95,
      "max_steps": 30,
      "mode": "primary"
    }
  },

  "permission": [
    {"tool": "read",  "action": "allow"},
    {"tool": "bash",  "action": "deny",  "pattern": "rm *"},
    {"tool": "bash",  "action": "ask"}
  ],

  "instructions": [
    "Always write tests alongside new code.",
    "Prefer async/await over callbacks.",
    "Use British English in all documentation."
  ],

  "default_agent": "build",

  "mcp": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
      "env": {}
    },
    "github": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"}
    },
    "remote": {
      "type": "sse",
      "url": "http://localhost:8080/mcp",
      "headers": {"Authorization": "Bearer ${MCP_TOKEN}"}
    }
  }
}
```

## Field reference

### `model`

The default model used when no agent-specific model is set.

```json
{"provider_id": "anthropic", "model_id": "claude-sonnet-4-6"}
```

### `provider`

Per-provider configuration. Keys are provider IDs recognised by litellm.

| Field | Description |
|-------|-------------|
| `api_key` | API key (supports `${VAR}` expansion) |
| `base_url` | Custom base URL (Azure, local proxy, Ollama, etc.) |
| `api_version` | API version string (Azure OpenAI) |
| `options` | Arbitrary options forwarded to litellm |

### `agent`

Named agent overrides. Keys are agent names. See [Agents](agents.md) for field details.

The `prompt` field is **appended** to the built-in system prompt (not replacing it).

### `permission`

Project-level permission rules. See [Permissions](permissions.md) for full details.

Rules are evaluated in the order they are listed, after agent-level rules.

### `instructions`

List of strings appended to every agent's system prompt. Use for project-wide conventions.

```json
{"instructions": ["This is a TypeScript project. Always use strict types."]}
```

Multiple config files contribute their `instructions` lists (they are concatenated, not overwritten).

### `default_agent`

Name of the agent used when none is specified. Defaults to `"build"`.

### `mcp`

MCP server connections. See [MCP](mcp.md).

## Environment variable expansion

`${VAR}` references in any string config value are expanded from the environment at load time:

```json
{"api_key": "${ANTHROPIC_API_KEY}"}
```

If `VAR` is not set, the literal string `${VAR}` is kept (no error).

Standard provider keys are also read directly from the environment by litellm without needing to configure them:

| Variable | Provider |
|----------|----------|
| `ANTHROPIC_API_KEY` | Anthropic |
| `OPENAI_API_KEY` | OpenAI |
| `AZURE_API_KEY` | Azure OpenAI |
| `GEMINI_API_KEY` | Google Gemini |

## Switching model live

From inside a session:

```
/model                                      # show current model
/model anthropic/claude-opus-4-6            # switch for this session
/model openai/gpt-4o --project             # switch and save to openvibe.json
/model ollama/llama3.2 --global            # switch and save to global config
```

## Initialising a project config

```
/init
```

Creates a minimal `openvibe.json` in the project directory if one does not already exist. If one exists, shows its contents.

## Viewing effective config

```
/config
```

Shows the fully resolved configuration for the current session, including which config files were loaded.

## Comments in config

Use `.jsonc` extension to allow `//` and `/* */` comments:

```jsonc
{
  // Use Sonnet for cost efficiency on routine tasks
  "model": {"provider_id": "anthropic", "model_id": "claude-sonnet-4-6"},
  "permission": [
    // Always allow reads — they're safe
    {"tool": "read", "action": "allow"}
  ]
}
```
