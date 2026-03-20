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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from openvibe.agent import agent as agent_module
from openvibe.config import Config, PermissionAction, load_config
from openvibe.core import AppState, create_app_state
from openvibe.db import Database, create_database
from openvibe.llm import LLMBackend, create_default_backend
from openvibe.provider import provider as provider_module
from openvibe.session import session as session_store
from openvibe.session.models import SessionInfo


# ---------------------------------------------------------------------------
# FastAPI dependency injection
# ---------------------------------------------------------------------------

_state: AppState | None = None


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
        await state.bus.publish(SessionCreatedEvent(session_id=session.id, session=session))
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
    async def update_session(session_id: str, body: UpdateSessionRequest, state: State) -> SessionInfo:
        session = session_store.get(state.db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        session_store.update_title(state.db, session_id, body.title)
        updated = session_store.get(state.db, session_id)
        assert updated
        from openvibe.session.models import SessionUpdatedEvent
        await state.bus.publish(SessionUpdatedEvent(session_id=session_id, session=updated))
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

        async def event_stream() -> AsyncGenerator[dict[str, Any], None]:
            task = asyncio.create_task(
                state.processor.run(session, resolved_agent, body.text, abort)
            )

            async with state.bus.subscribe() as events:
                async for event in events:
                    if getattr(event, "session_id", None) == session_id:
                        yield {
                            "event": type(event).__name__,
                            "data": _serialize_event(event),
                        }
                    if task.done():
                        break

            # Ensure the task completes and surface any exception
            try:
                await task
            except Exception as exc:
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}

        return EventSourceResponse(event_stream())

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
            {"name": s.name, "connected": s.connected, "tools": s.tools, "error": s.error}
            for s in state.mcp.status()
        ]

    # ------------------------------------------------------------------
    # Permission routes
    # ------------------------------------------------------------------

    @app.post("/permission/reply")
    async def reply_permission(body: PermissionReplyRequest, state: State) -> dict[str, str]:
        if body.decision == PermissionAction.ASK:
            raise HTTPException(status_code=400, detail="decision must be 'allow' or 'deny'")
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
            {"name": t.name, "description": t.description, "parameters": t.parameters_schema()}
            for t in state.registry.all()
        ]

    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_event(event: Any) -> str:
    """Convert a bus event dataclass to a JSON string."""
    try:
        from dataclasses import asdict, fields
        d = asdict(event) if hasattr(event, "__dataclass_fields__") else {}
        # Include nested Pydantic models
        return json.dumps(d, default=str)
    except Exception:
        return json.dumps({"type": type(event).__name__})
