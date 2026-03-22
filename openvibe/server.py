"""FastAPI HTTP server.

Exposes the openvibe engine over a REST + SSE API so any client (CLI, TUI,
web, mobile) can drive sessions remotely.

All mutating routes return the updated resource.
Streaming routes use Server-Sent Events (SSE) via ``sse-starlette``.

Routes
------
Session management
    POST   /session                    create a new session
    GET    /session                    list sessions for the current project
    GET    /session/{id}               get a single session
    DELETE /session/{id}               archive a session
    PATCH  /session/{id}               update title
    GET    /session/{id}/messages      get all messages (with parts)
    POST   /session/{id}/message       send a message (SSE stream)
    GET    /session/{id}/state         current state + pending permission info
    POST   /session/{id}/abort         cancel an in-flight turn
    POST   /session/{id}/resume        resume a session interrupted mid-tool

Provider / model info
    GET    /provider                   list all providers
    GET    /provider/{id}/model        list models for a provider
    GET    /model                      list all known models

Configuration
    GET    /config                     return the loaded config (redacted)

MCP
    GET    /mcp                        list MCP server statuses

Permission
    POST   /permission/reply           reply to a pending permission request

Events (SSE)
    GET    /events                     global event stream (all sessions)
    GET    /events/{session_id}        filtered event stream for one session

Health
    GET    /health                     liveness check
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from openvibe.agent import agent as agent_module
from openvibe.bus import EventBus
from openvibe.config import Config, PermissionAction, load_config
from openvibe.db import Database, create_database
from openvibe.llm import LLMBackend, create_default_backend
from openvibe.mcp.client import McpClientManager
from openvibe.permission.permission import PermissionService
from openvibe.project import project as project_module
from openvibe.provider import provider as provider_module
from openvibe.session import session as session_store
from openvibe.session.models import SessionInfo
from openvibe.session.processor import SessionProcessor
from openvibe.tool.base import ToolRegistry, create_default_registry

# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------


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
    """Async context manager that yields a fully initialised ``AppState``."""
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


# ---------------------------------------------------------------------------
# FastAPI dependency injection
# ---------------------------------------------------------------------------

_state: AppState | None = None

# Tracks in-flight turns: session_id → abort asyncio.Event.
# Set when a turn starts, cleared when it ends.
_active_aborts: dict[str, asyncio.Event] = {}

# Tracks pending permission requests: session_id → permission info dict.
# Populated from PermissionRequestedEvent bus events, cleared on reply/end.
_pending_permissions: dict[str, dict] = {}


def get_state() -> AppState:
    if _state is None:
        raise RuntimeError("App not initialised")
    return _state


State = Annotated[AppState, Depends(get_state)]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    title: str | None = None
    agent: str | None = None
    parent_id: str | None = None


class SendMessageRequest(BaseModel):
    text: str
    agent: str | None = None


class UpdateSessionRequest(BaseModel):
    title: str


class PermissionReplyRequest(BaseModel):
    request_id: str
    decision: PermissionAction
    remember: bool = False
    project_id: str | None = None
    tool: str | None = None


class ResumeSessionRequest(BaseModel):
    allow: bool
    agent: str | None = None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    project_dir: Path | None = None,
    config: Config | None = None,
    db: Database | None = None,
    llm: LLMBackend | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        global _state
        _active_aborts.clear()
        _pending_permissions.clear()
        async with create_app_state(
            project_dir=project_dir,
            config=config,
            db=db,
            llm=llm,
        ) as state:
            _state = state
            yield
            _state = None

    app = FastAPI(
        title="openvibe",
        version="0.1.0",
        description="Open-source AI coding agent API",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:*", "https://*.openvibe.ai"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # Session routes
    # ------------------------------------------------------------------

    @app.post("/session", response_model=SessionInfo)
    async def create_session(body: CreateSessionRequest, state: State) -> SessionInfo:
        project = project_module.get_or_create(state.db, state.project_dir)
        session = session_store.create(
            state.db,
            project_id=project.id,
            directory=str(state.project_dir),
            parent_id=body.parent_id,
            title=body.title,
        )
        from openvibe.session.models import SessionCreatedEvent

        await state.bus.publish(
            SessionCreatedEvent(session_id=session.id, session=session)
        )
        return session

    @app.get("/session", response_model=list[SessionInfo])
    async def list_sessions(state: State) -> list[SessionInfo]:
        project = project_module.get_or_create(state.db, state.project_dir)
        return session_store.list_sessions(state.db, project.id)

    @app.get("/session/{session_id}", response_model=SessionInfo)
    async def get_session(session_id: str, state: State) -> SessionInfo:
        session = session_store.get(state.db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    @app.patch("/session/{session_id}", response_model=SessionInfo)
    async def update_session(
        session_id: str, body: UpdateSessionRequest, state: State
    ) -> SessionInfo:
        session = session_store.get(state.db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        session_store.update_title(state.db, session_id, body.title)
        updated = session_store.get(state.db, session_id)
        assert updated
        from openvibe.session.models import SessionUpdatedEvent

        await state.bus.publish(
            SessionUpdatedEvent(session_id=session_id, session=updated)
        )
        return updated

    @app.delete("/session/{session_id}")
    async def delete_session(session_id: str, state: State) -> dict[str, str]:
        session = session_store.get(state.db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        session_store.archive(state.db, session_id)
        return {"status": "archived"}

    @app.get("/session/{session_id}/messages")
    async def get_messages(session_id: str, state: State) -> list[dict[str, Any]]:
        session = session_store.get(state.db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        messages = session_store.list_messages(state.db, session_id)
        return [m.model_dump() for m in messages]

    @app.post("/session/{session_id}/message")
    async def send_message(
        session_id: str,
        body: SendMessageRequest,
        state: State,
    ) -> EventSourceResponse:
        """Send a user message and stream the assistant response via SSE."""
        session = session_store.get(state.db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        agent_name = body.agent or state.config.default_agent
        resolved_agent = agent_module.resolve(state.config, agent_name)

        abort = asyncio.Event()
        coro = state.processor.run(session, resolved_agent, body.text, abort)
        return EventSourceResponse(_make_event_stream(coro, session_id, state, abort))

    @app.get("/session/{session_id}/state")
    async def get_session_state(session_id: str, state: State) -> dict[str, Any]:
        """Return the current runtime state of a session.

        States:
        - ``idle``        — no active turn; ready to accept a message.
        - ``thinking``    — a turn is running (LLM is responding or a tool is running).
        - ``waiting``     — paused; a permission prompt is awaiting the user's reply.
        - ``interrupted`` — the app was closed mid-tool; use POST /resume to continue.
        """
        from openvibe.session.models import ToolPart

        session = session_store.get(state.db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if session_id in _active_aborts:
            current_state = (
                "waiting" if session_id in _pending_permissions else "thinking"
            )
        else:
            messages = session_store.list_messages(state.db, session_id)
            interrupted = any(
                isinstance(part, ToolPart)
                and part.state.call_id
                and part.state.output is None
                for msg in messages
                for part in msg.parts
            )
            current_state = "interrupted" if interrupted else "idle"

        return {
            "state": current_state,
            "pending_permission": _pending_permissions.get(session_id),
        }

    @app.post("/session/{session_id}/abort")
    async def abort_session(session_id: str, state: State) -> dict[str, str]:
        """Cancel the in-flight turn for a session.

        Sets the abort event so the processor exits at its next checkpoint.
        Returns ``{"status": "ok"}`` if a turn was active, or
        ``{"status": "no_active_turn"}`` if the session was already idle.
        """
        session = session_store.get(state.db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        abort = _active_aborts.get(session_id)
        if abort:
            abort.set()
            return {"status": "ok"}
        return {"status": "no_active_turn"}

    @app.post("/session/{session_id}/resume")
    async def resume_session(
        session_id: str,
        body: ResumeSessionRequest,
        state: State,
    ) -> EventSourceResponse:
        """Resume a session that was interrupted mid-tool.

        When the server (or TUI) is closed while a permission prompt is
        pending, the ToolPart is saved with ``output=None``.  This endpoint
        executes the interrupted tool (``allow=true``) or injects a denied
        result (``allow=false``), then streams the LLM's final response.

        Returns 400 if the session has no interrupted tool calls.
        """
        from openvibe.session.models import ToolPart

        session = session_store.get(state.db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        messages = session_store.list_messages(state.db, session_id)
        interrupted = any(
            isinstance(part, ToolPart)
            and part.state.call_id
            and part.state.output is None
            for msg in messages
            for part in msg.parts
        )
        if not interrupted:
            raise HTTPException(
                status_code=400, detail="Session has no interrupted tool calls"
            )

        agent_name = body.agent or state.config.default_agent
        resolved_agent = agent_module.resolve(state.config, agent_name)

        abort = asyncio.Event()
        coro = state.processor.resume_interrupted(
            session, resolved_agent, body.allow, abort
        )
        return EventSourceResponse(_make_event_stream(coro, session_id, state, abort))

    # ------------------------------------------------------------------
    # Provider / model routes
    # ------------------------------------------------------------------

    @app.get("/provider")
    async def list_providers() -> list[dict[str, Any]]:
        return [
            {"id": p.id, "name": p.name, "env_key": p.env_key}
            for p in provider_module.list_providers()
        ]

    @app.get("/provider/{provider_id}/model")
    async def list_provider_models(provider_id: str) -> list[dict[str, Any]]:
        provider = provider_module.get_provider(provider_id)
        if not provider:
            raise HTTPException(status_code=404, detail="Provider not found")
        return [m.__dict__ for m in provider.models]

    @app.get("/model")
    async def list_all_models() -> list[dict[str, Any]]:
        return [m.__dict__ for m in provider_module.list_models()]

    # ------------------------------------------------------------------
    # Config route
    # ------------------------------------------------------------------

    @app.get("/config")
    async def get_config(state: State) -> dict[str, Any]:
        cfg = state.config.model_dump()
        # Redact API keys from the response
        for provider_cfg in cfg.get("provider", {}).values():
            if provider_cfg.get("api_key"):
                provider_cfg["api_key"] = "***"
        return cfg

    # ------------------------------------------------------------------
    # MCP routes
    # ------------------------------------------------------------------

    @app.get("/mcp")
    async def get_mcp_status(state: State) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "connected": s.connected,
                "tools": s.tools,
                "error": s.error,
            }
            for s in state.mcp.status()
        ]

    # ------------------------------------------------------------------
    # Permission routes
    # ------------------------------------------------------------------

    @app.post("/permission/reply")
    async def reply_permission(
        body: PermissionReplyRequest, state: State
    ) -> dict[str, str]:
        if body.decision == PermissionAction.ASK:
            raise HTTPException(
                status_code=400, detail="decision must be 'allow' or 'deny'"
            )
        state.permissions.reply(
            request_id=body.request_id,
            decision=body.decision,
            remember=body.remember,
            project_id=body.project_id,
            tool=body.tool,
        )
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # Event streams (SSE)
    # ------------------------------------------------------------------

    @app.get("/events")
    async def global_events(state: State) -> EventSourceResponse:
        """Stream all bus events as SSE."""

        async def stream() -> AsyncGenerator[dict[str, Any], None]:
            async with state.bus.subscribe() as events:
                async for event in events:
                    yield {
                        "event": type(event).__name__,
                        "data": _serialize_event(event),
                    }

        return EventSourceResponse(stream())

    @app.get("/events/{session_id}")
    async def session_events(session_id: str, state: State) -> EventSourceResponse:
        """Stream bus events filtered to one session."""

        async def stream() -> AsyncGenerator[dict[str, Any], None]:
            async with state.bus.subscribe() as events:
                async for event in events:
                    if getattr(event, "session_id", None) == session_id:
                        yield {
                            "event": type(event).__name__,
                            "data": _serialize_event(event),
                        }

        return EventSourceResponse(stream())

    # ------------------------------------------------------------------
    # Tools route
    # ------------------------------------------------------------------

    @app.get("/tool")
    async def list_tools(state: State) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters_schema(),
            }
            for t in state.registry.all()
        ]

    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_event_stream(
    coro: Any,
    session_id: str,
    state: AppState,
    abort: asyncio.Event,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run *coro* (a processor coroutine) and stream bus events as SSE.

    Registers the abort event in ``_active_aborts`` for the duration of the
    turn and updates ``_pending_permissions`` from ``PermissionRequestedEvent``
    and ``PermissionRepliedEvent`` bus events.
    """
    from openvibe.permission.permission import (PermissionRepliedEvent,
                                                PermissionRequestedEvent)
    from openvibe.session.models import TurnCompletedEvent

    _active_aborts[session_id] = abort
    try:
        task = asyncio.create_task(coro)

        async with state.bus.subscribe() as events:
            async for event in events:
                if getattr(event, "session_id", None) == session_id:
                    # Keep permission state in sync.
                    if isinstance(event, PermissionRequestedEvent):
                        _pending_permissions[session_id] = {
                            "request_id": event.request_id,
                            "tool": event.tool,
                            "description": event.description,
                            "argument": event.argument,
                        }
                    elif isinstance(event, PermissionRepliedEvent):
                        _pending_permissions.pop(session_id, None)

                    yield {
                        "event": type(event).__name__,
                        "data": _serialize_event(event),
                    }
                if task.done():
                    break

        try:
            await task
        except Exception as exc:
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}
    finally:
        _active_aborts.pop(session_id, None)
        _pending_permissions.pop(session_id, None)


def _serialize_event(event: Any) -> str:
    """Convert a bus event dataclass to a JSON string."""
    try:
        from dataclasses import asdict, fields

        d = asdict(event) if hasattr(event, "__dataclass_fields__") else {}
        # Include nested Pydantic models
        return json.dumps(d, default=str)
    except Exception:
        return json.dumps({"type": type(event).__name__})
