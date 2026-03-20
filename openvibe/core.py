"""Shared application state.

Both the HTTP server and the TUI construct an ``AppState`` via
``create_app_state``.  This keeps all wiring in one place so neither
consumer duplicates it.

Usage::

    async with create_app_state(project_dir=Path.cwd()) as state:
        # state.db, state.bus, state.processor, state.permissions …
        await state.processor.run(session, agent, text)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from pathlib import Path

from openvibe.bus import EventBus
from openvibe.config import Config, load_config
from openvibe.db import Database, create_database
from openvibe.llm import LLMBackend, create_default_backend
from openvibe.mcp.client import McpClientManager
from openvibe.permission.permission import PermissionService
from openvibe.project import project as project_module
from openvibe.session.processor import SessionProcessor
from openvibe.tool.base import ToolRegistry, create_default_registry


class AppState:
    """All live objects that make up one running openvibe instance."""

    def __init__(
        self,
        db: Database,
        llm: LLMBackend,
        bus: EventBus,
        config: Config,
        registry: ToolRegistry,
        permissions: PermissionService,
        mcp: McpClientManager,
        project_dir: Path,
    ) -> None:
        self.db = db
        self.llm = llm
        self.bus = bus
        self.config = config
        self.registry = registry
        self.permissions = permissions
        self.mcp = mcp
        self.project_dir = project_dir
        self._processor = SessionProcessor(db, llm, bus, registry, permissions)

    @property
    def processor(self) -> SessionProcessor:
        return self._processor


@asynccontextmanager
async def create_app_state(
    project_dir: Path | None = None,
    config: Config | None = None,
    db: Database | None = None,
    llm: LLMBackend | None = None,
) -> AsyncGenerator[AppState, None]:
    """Async context manager that yields a fully initialised ``AppState``.

    Connects MCP servers on entry and tears them down (along with the DB
    connection) on exit.
    """
    resolved_dir = project_dir or Path.cwd()
    resolved_config = config or load_config(resolved_dir)
    resolved_db = db or create_database()
    resolved_llm = llm or create_default_backend()

    bus = EventBus()
    registry = create_default_registry()
    permissions = PermissionService(resolved_db, bus)
    mcp = McpClientManager()

    project_module.get_or_create(resolved_db, resolved_dir)

    mcp_tools = await mcp.connect_all(resolved_config.mcp)
    for tool in mcp_tools:
        registry.register(tool)

    state = AppState(
        db=resolved_db,
        llm=resolved_llm,
        bus=bus,
        config=resolved_config,
        registry=registry,
        permissions=permissions,
        mcp=mcp,
        project_dir=resolved_dir,
    )

    try:
        yield state
    finally:
        await mcp.close_all()
        resolved_db.close()
