# MCP — Model Context Protocol

openvibe supports the [Model Context Protocol](https://modelcontextprotocol.io) (MCP). MCP servers expose tools and data sources that the agent can call alongside built-in tools.

## Configuration

Add MCP servers to `openvibe.json` under the `mcp` key:

```json
{
  "mcp": {
    "server-name": { ... }
  }
}
```

MCP tools are registered at startup alongside all built-in tools and are available to every agent.

## Transport types

### stdio

The server is a subprocess. openvibe starts it, communicates over stdin/stdout, and keeps it alive for the session.

```json
{
  "mcp": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/docs"],
      "env": {
        "SOME_VAR": "${SOME_VAR}"
      }
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"stdio"` | Transport type |
| `command` | `str` | Executable to run |
| `args` | `list[str]` | Arguments passed to the command |
| `env` | `dict[str,str]` | Extra environment variables for the subprocess |

### SSE / HTTP

The server is a remote HTTP endpoint. openvibe connects via Server-Sent Events.

```json
{
  "mcp": {
    "remote-tools": {
      "type": "sse",
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_TOKEN}"
      }
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"sse"` | Transport type |
| `url` | `str` | MCP server endpoint URL |
| `headers` | `dict[str,str]` | HTTP headers (e.g. auth) |

## Common MCP servers

### Official servers (via npm)

```json
{
  "mcp": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
    },
    "github": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"}
    },
    "postgres": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres", "${DATABASE_URL}"]
    },
    "slack": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-slack"],
      "env": {"SLACK_BOT_TOKEN": "${SLACK_BOT_TOKEN}"}
    }
  }
}
```

### Python-based servers (via uvx)

```json
{
  "mcp": {
    "git": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-server-git", "--repository", "/path/to/repo"]
    }
  }
}
```

## How MCP tools appear to the agent

Each tool exposed by an MCP server is registered in the tool registry with its schema. The agent sees them identically to built-in tools. Tool names are scoped to avoid collisions: a tool called `read_file` on a server named `filesystem` is registered as `filesystem__read_file` (double underscore separator).

## Permission rules for MCP tools

MCP tools follow the same permission system as built-in tools. Add rules using the full scoped name or a glob:

```json
{
  "permission": [
    {"tool": "filesystem__*", "action": "allow"},
    {"tool": "github__*",     "action": "ask"}
  ]
}
```

## Programmatic MCP access

```python
from openvibe import OpenVibe

# MCP connections are established during start_async()
# (the TUI path) or start() (the sync path)
async with OpenVibe() as ov:
    await ov.start_async()
    session = ov.create_session()
    # MCP tools are already available
    response = session.send("List files in /data using the filesystem tool")
```
