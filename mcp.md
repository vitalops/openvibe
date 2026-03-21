# MCP Integration in openvibe

MCP (Model Context Protocol) is an open standard that lets external programs expose tools to AI agents over a well-defined wire protocol. openvibe uses the `mcp` Python SDK to connect to MCP servers at startup and register their tools into the same registry used by built-in tools like `bash`, `read`, and `write`.

---

## How it works end-to-end

```
openvibe.json
  └── "mcp": { "server-name": { ... } }
          │
          ▼
    McpClientManager.connect_all()          ← runs at startup
          │
          ├── stdio transport: spawns a subprocess, talks over stdin/stdout
          └── sse transport:   connects to a remote HTTP SSE endpoint
                    │
                    ▼
              ClientSession.list_tools()     ← MCP handshake
                    │
                    ▼
              McpTool("mcp__<server>__<tool>")  ← wrapped as openvibe Tool
                    │
                    ▼
              ToolRegistry.register(mcp_tool)   ← available to agents
```

When the LLM requests a tool call the processor looks it up in `ToolRegistry` by name. If the name is `mcp__<server>__<tool>`, the call is forwarded to `ClientSession.call_tool()` on the connected MCP session. The result is returned to the LLM as a normal tool result.

---

## Configuration

Add an `"mcp"` key to `openvibe.json` (in your project directory or `~/.config/openvibe/openvibe.json` for global tools). Each entry is a named MCP server.

### stdio transport (local process)

Spawn a local program that speaks MCP over stdin/stdout. This is the most common transport for command-line MCP servers.

```json
{
  "mcp": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/projects"],
      "env": {
        "MY_VAR": "value"
      }
    }
  }
}
```

| Field     | Type            | Required | Description                               |
|-----------|-----------------|----------|-------------------------------------------|
| `type`    | `"stdio"`       | yes      | Transport type                            |
| `command` | string          | yes      | Executable to run (e.g. `npx`, `python`)  |
| `args`    | list of strings | no       | Arguments passed to the command           |
| `env`     | object          | no       | Extra environment variables for the child |

### SSE transport (remote HTTP server)

Connect to an HTTP server that streams events using Server-Sent Events.

```json
{
  "mcp": {
    "my-api": {
      "type": "sse",
      "url": "http://localhost:8080/sse",
      "headers": {
        "Authorization": "Bearer ${MY_API_TOKEN}"
      }
    }
  }
}
```

| Field     | Type   | Required | Description                                 |
|-----------|--------|----------|---------------------------------------------|
| `type`    | `"sse"`| yes      | Transport type                              |
| `url`     | string | yes      | Full URL of the SSE endpoint                |
| `headers` | object | no       | HTTP headers sent with every request        |

---

## Multiple servers

You can connect to any number of MCP servers simultaneously. Each gets its own namespace in the tool name:

```json
{
  "mcp": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    "github": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}" }
    },
    "my-remote": {
      "type": "sse",
      "url": "https://mcp.example.com/sse"
    }
  }
}
```

---

## Tool naming convention

Every tool discovered from an MCP server is registered under the name:

```
mcp__{server_name}__{tool_name}
```

For example, if a server named `filesystem` exposes a tool called `read_file`, openvibe registers it as:

```
mcp__filesystem__read_file
```

The description shown to the LLM is prefixed with the server name:

```
[filesystem] Read the contents of a file.
```

This namespacing means tools from different MCP servers never collide with each other or with built-in tools.

---

## Permission rules for MCP tools

MCP tools go through the same permission system as built-in tools. Use the full tool name in your permission rules:

```json
{
  "permission": [
    { "tool": "mcp__filesystem__write_file", "action": "ask" },
    { "tool": "mcp__filesystem__read_file",  "action": "allow" },
    { "tool": "mcp__github__*",              "action": "ask" }
  ]
}
```

Glob patterns (`*`) work — `mcp__github__*` matches all tools from the `github` server.

---

## Checking connection status

### Via the REST API

```
GET /mcp
```

Returns a list of server statuses:

```json
[
  {
    "name": "filesystem",
    "connected": true,
    "tools": ["mcp__filesystem__read_file", "mcp__filesystem__write_file"],
    "error": null
  },
  {
    "name": "broken-server",
    "connected": false,
    "tools": [],
    "error": "Connection timed out."
  }
]
```

### Via the synchronous API

```python
from openvibe.api import OpenVibe
from openvibe.config import Config

config = Config.model_validate({
    "mcp": {
        "filesystem": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        }
    }
})

with OpenVibe(config=config) as ov:
    resp = ov.run("list the files in /tmp using the filesystem tool")
    print(resp.text)
```

---

## Writing your own MCP server

Any program that implements the MCP protocol can be used. Here is a minimal Python MCP server using the official `mcp` SDK:

```python
# my_server.py
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import asyncio

app = Server("my-server")

@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="greet",
            description="Return a greeting for the given name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name to greet"}
                },
                "required": ["name"]
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "greet":
        return [TextContent(type="text", text=f"Hello, {arguments['name']}!")]
    raise ValueError(f"Unknown tool: {name}")

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
```

Register it in `openvibe.json`:

```json
{
  "mcp": {
    "my-server": {
      "type": "stdio",
      "command": "python",
      "args": ["my_server.py"]
    }
  }
}
```

openvibe will register `mcp__my-server__greet` in the tool registry. The agent can then call it like any other tool.

---

## Startup behaviour

- All MCP servers are connected **concurrently** at startup (`asyncio.gather`).
- Each connection has a **30 second timeout**. If a server does not respond within 30 s, it is marked as disconnected and openvibe continues without it. No error is raised; a warning is logged.
- If the `mcp` key is absent from config, no connections are attempted and startup is instant.
- On shutdown (`__exit__` / `close_async`), all MCP sessions are closed cleanly.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Tool not appearing in agent | Server failed to connect | Check `GET /mcp` for `error` field; check that the command exists and is on `PATH` |
| `Connection timed out` | Server process starts but doesn't respond | Ensure server speaks MCP protocol; test with `mcp dev my_server.py` |
| Tool call returns `MCP server not connected.` | Session was lost after initial connect | Restart openvibe; check server process is still alive |
| `command` not found | Executable missing | Install the package (`npm install -g @modelcontextprotocol/server-filesystem`) or use an absolute path |
| Auth errors with SSE | Missing / wrong `headers` | Add `Authorization` header in config; use env var substitution for secrets |
