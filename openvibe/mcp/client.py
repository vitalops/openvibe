"""MCP (Model Context Protocol) client integration.

Connects to external MCP servers and exposes their tools to the openvibe
tool registry at session startup.

Supported transports
--------------------
- ``stdio`` — spawn a local process and communicate over stdin/stdout
- ``sse``   — connect to a remote HTTP server using Server-Sent Events

Configuration example (``openvibe.json``)::

    {
      "mcp": {
        "filesystem": {
          "type": "stdio",
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user"]
        },
        "my-api": {
          "type": "sse",
          "url": "http://localhost:8080/sse"
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openvibe.tool.base import Tool, ToolContext, ToolResult

if TYPE_CHECKING:
    from openvibe.config import McpServerConfig

logger = logging.getLogger(__name__)

MCP_TIMEOUT = 30.0  # seconds


# ---------------------------------------------------------------------------
# MCP-backed tool wrapper
# ---------------------------------------------------------------------------

class McpTool(Tool):
    """Wraps an MCP server tool as a first-class openvibe Tool."""

    def __init__(self, server_name: str, tool_name: str, tool_description: str, schema: dict[str, Any]) -> None:
        self.name = f"mcp__{server_name}__{tool_name}"
        self.description = f"[{server_name}] {tool_description}"
        self._server_name = server_name
        self._tool_name = tool_name
        self._schema = schema
        self._session: Any = None  # set by McpClientManager

    def parameters_schema(self) -> dict[str, Any]:
        return self._schema

    async def execute(self, ctx: ToolContext, params: "McpTool.Params") -> ToolResult:
        if not self._session:
            return ToolResult(
                title=f"MCP tool: {self._tool_name}",
                output="MCP server not connected.",
                error=True,
            )
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(self._tool_name, dict(params)),
                timeout=MCP_TIMEOUT,
            )
            # MCP result is a list of content blocks
            text = "\n".join(
                block.text for block in result.content if hasattr(block, "text")
            )
            return ToolResult(title=f"MCP: {self._tool_name}", output=text or "(no output)")
        except asyncio.TimeoutError:
            return ToolResult(
                title=f"MCP: {self._tool_name}",
                output=f"Tool call timed out after {MCP_TIMEOUT}s.",
                error=True,
            )
        except Exception as exc:
            return ToolResult(title=f"MCP: {self._tool_name}", output=str(exc), error=True)

    async def __call__(self, ctx: ToolContext, raw_args: str | dict[str, Any]) -> ToolResult:
        import json
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}
        else:
            args = raw_args
        # Pass args directly — MCP handles its own schema validation
        return await self.execute(ctx, args)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Client manager
# ---------------------------------------------------------------------------

@dataclass
class McpServerStatus:
    name: str
    connected: bool
    tools: list[str] = field(default_factory=list)
    error: str | None = None


class McpClientManager:
    """Manages connections to all configured MCP servers."""

    def __init__(self) -> None:
        self._connections: dict[str, Any] = {}   # name → (read, write, session) tuple
        self._tools: list[McpTool] = []
        self._status: dict[str, McpServerStatus] = {}

    async def connect_all(
        self, configs: dict[str, "McpServerConfig"]
    ) -> list[McpTool]:
        """Connect to all configured MCP servers; return discovered tools."""
        tasks = [self._connect_one(name, cfg) for name, cfg in configs.items()]
        await asyncio.gather(*tasks, return_exceptions=True)
        return self._tools

    async def _connect_one(self, name: str, cfg: "McpServerConfig") -> None:
        try:
            tools = await asyncio.wait_for(
                self._do_connect(name, cfg),
                timeout=MCP_TIMEOUT,
            )
            self._status[name] = McpServerStatus(
                name=name, connected=True, tools=[t.name for t in tools]
            )
        except asyncio.TimeoutError:
            logger.warning("MCP server '%s' connection timed out.", name)
            self._status[name] = McpServerStatus(
                name=name, connected=False, error="Connection timed out."
            )
        except Exception as exc:
            logger.warning("MCP server '%s' failed to connect: %s", name, exc)
            self._status[name] = McpServerStatus(
                name=name, connected=False, error=str(exc)
            )

    async def _do_connect(self, name: str, cfg: "McpServerConfig") -> list[McpTool]:
        from mcp import ClientSession

        if cfg.type == "stdio":
            from mcp.client.stdio import StdioServerParameters, stdio_client

            if not cfg.command:
                raise ValueError(f"MCP server '{name}': 'command' is required for stdio type.")

            params = StdioServerParameters(
                command=cfg.command,
                args=cfg.args,
                env=cfg.env or None,
            )
            read, write = await stdio_client(params).__aenter__()

        elif cfg.type == "sse":
            from mcp.client.sse import sse_client

            if not cfg.url:
                raise ValueError(f"MCP server '{name}': 'url' is required for sse type.")

            read, write = await sse_client(cfg.url, headers=cfg.headers).__aenter__()

        else:
            raise ValueError(f"MCP server '{name}': unknown type '{cfg.type}'.")

        session = ClientSession(read, write)
        await session.initialize()
        self._connections[name] = session

        # Discover tools
        tool_list = await session.list_tools()
        mcp_tools: list[McpTool] = []
        for t in tool_list.tools:
            schema = t.inputSchema if hasattr(t, "inputSchema") else {}
            mcp_tool = McpTool(
                server_name=name,
                tool_name=t.name,
                tool_description=t.description or "",
                schema=schema,
            )
            mcp_tool._session = session
            mcp_tools.append(mcp_tool)
            self._tools.append(mcp_tool)

        return mcp_tools

    def status(self) -> list[McpServerStatus]:
        return list(self._status.values())

    async def close_all(self) -> None:
        for session in self._connections.values():
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                pass
        self._connections.clear()
        self._tools.clear()
