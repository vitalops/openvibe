"""Unit tests for openvibe.server (FastAPI routes).

All tests inject a _FakeLLM and a temp-file SQLite DB so no network calls
are made.  A TestClient is used which drives the ASGI app synchronously via
anyio, including proper lifespan startup/shutdown.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openvibe.config import Config, PermissionAction, ProviderConfig
from openvibe.db import create_database
from openvibe.llm import LLMEvent, StreamDone, TextDelta
from openvibe.server import _serialize_event, create_app

# ---------------------------------------------------------------------------
# Fake async LLM backend
# ---------------------------------------------------------------------------


class _FakeLLM:
    """Immediately yields a fixed text response with no tool calls."""

    def __init__(self, text: str = "hello") -> None:
        self._text = text

    async def stream(
        self,
        model: str,
        messages: list[Any],
        **kwargs: Any,
    ) -> AsyncIterator[LLMEvent]:
        yield TextDelta(content=self._text)
        yield StreamDone()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path):
    db = create_database(tmp_path / "test.db")
    app = create_app(project_dir=tmp_path, config=Config(), db=db, llm=_FakeLLM())
    with TestClient(app) as c:
        yield c


def _create_session(client: TestClient, **kwargs: Any) -> dict:
    """POST /session and return the parsed JSON body."""
    resp = client.post("/session", json=kwargs)
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


def test_create_session_returns_session_info(client: TestClient) -> None:
    resp = client.post("/session", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["title"] is None


def test_create_session_with_title(client: TestClient) -> None:
    resp = client.post("/session", json={"title": "my session"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "my session"


def test_create_session_with_parent(client: TestClient) -> None:
    parent = _create_session(client, title="parent")
    child = _create_session(client, parent_id=parent["id"])
    assert child["parent_id"] == parent["id"]


def test_list_sessions_empty(client: TestClient) -> None:
    resp = client.get("/session")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_sessions_after_create(client: TestClient) -> None:
    _create_session(client)
    _create_session(client)
    resp = client.get("/session")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_get_session(client: TestClient) -> None:
    created = _create_session(client, title="test")
    resp = client.get(f"/session/{created['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == created["id"]
    assert data["title"] == "test"


def test_get_session_not_found(client: TestClient) -> None:
    resp = client.get("/session/nonexistent")
    assert resp.status_code == 404


def test_update_session_title(client: TestClient) -> None:
    created = _create_session(client)
    resp = client.patch(f"/session/{created['id']}", json={"title": "updated"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "updated"


def test_update_session_title_reflected_in_get(client: TestClient) -> None:
    created = _create_session(client)
    client.patch(f"/session/{created['id']}", json={"title": "new"})
    resp = client.get(f"/session/{created['id']}")
    assert resp.json()["title"] == "new"


def test_update_session_not_found(client: TestClient) -> None:
    resp = client.patch("/session/nonexistent", json={"title": "x"})
    assert resp.status_code == 404


def test_delete_session(client: TestClient) -> None:
    created = _create_session(client)
    resp = client.delete(f"/session/{created['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "archived"}


def test_delete_session_not_found(client: TestClient) -> None:
    resp = client.delete("/session/nonexistent")
    assert resp.status_code == 404


def test_get_messages_empty(client: TestClient) -> None:
    created = _create_session(client)
    resp = client.get(f"/session/{created['id']}/messages")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_messages_not_found(client: TestClient) -> None:
    resp = client.get("/session/nonexistent/messages")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Send message (SSE)
# ---------------------------------------------------------------------------


def test_send_message_not_found(client: TestClient) -> None:
    resp = client.post("/session/nonexistent/message", json={"text": "hi"})
    assert resp.status_code == 404


def test_send_message_returns_event_stream(client: TestClient) -> None:
    created = _create_session(client)
    resp = client.post(f"/session/{created['id']}/message", json={"text": "hello"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


def test_send_message_sse_contains_data_lines(client: TestClient) -> None:
    created = _create_session(client)
    resp = client.post(f"/session/{created['id']}/message", json={"text": "hello"})
    assert b"data:" in resp.content


def test_send_message_sse_contains_message_created_event(client: TestClient) -> None:
    created = _create_session(client)
    resp = client.post(f"/session/{created['id']}/message", json={"text": "hello"})
    # MessageCreatedEvent is always the first event published by the processor.
    assert "MessageCreatedEvent" in resp.text


def test_send_message_persists_user_and_assistant_messages(client: TestClient) -> None:
    created = _create_session(client)
    client.post(f"/session/{created['id']}/message", json={"text": "ping"})
    resp = client.get(f"/session/{created['id']}/messages")
    roles = {m["role"] for m in resp.json()}
    assert "user" in roles
    assert "assistant" in roles


def test_send_message_user_text_stored(client: TestClient) -> None:
    created = _create_session(client)
    client.post(f"/session/{created['id']}/message", json={"text": "stored text"})
    msgs = client.get(f"/session/{created['id']}/messages").json()
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert any("stored text" in str(m["parts"]) for m in user_msgs)


# ---------------------------------------------------------------------------
# Provider / model routes
# ---------------------------------------------------------------------------


def test_list_providers(client: TestClient) -> None:
    resp = client.get("/provider")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    if data:
        assert "id" in data[0]
        assert "name" in data[0]


def test_list_provider_models_not_found(client: TestClient) -> None:
    resp = client.get("/provider/__nonexistent__/model")
    assert resp.status_code == 404


def test_list_provider_models_known_provider(client: TestClient) -> None:
    providers = client.get("/provider").json()
    if not providers:
        pytest.skip("no providers registered")
    provider_id = providers[0]["id"]
    resp = client.get(f"/provider/{provider_id}/model")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_list_all_models(client: TestClient) -> None:
    resp = client.get("/model")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_get_config_returns_dict(client: TestClient) -> None:
    resp = client.get("/config")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


def test_get_config_redacts_api_key(tmp_path: Path) -> None:
    config = Config(provider={"openai": ProviderConfig(api_key="secret123")})
    db = create_database(tmp_path / "test.db")
    app = create_app(project_dir=tmp_path, config=config, db=db, llm=_FakeLLM())
    with TestClient(app) as c:
        resp = c.get("/config")
    assert resp.status_code == 200
    assert resp.json()["provider"]["openai"]["api_key"] == "***"


def test_get_config_null_api_key_not_changed(tmp_path: Path) -> None:
    config = Config(provider={"openai": ProviderConfig(api_key=None)})
    db = create_database(tmp_path / "test.db")
    app = create_app(project_dir=tmp_path, config=config, db=db, llm=_FakeLLM())
    with TestClient(app) as c:
        resp = c.get("/config")
    # api_key is None — must not be replaced with "***"
    assert resp.json()["provider"]["openai"]["api_key"] is None


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


def test_get_mcp_status(client: TestClient) -> None:
    resp = client.get("/mcp")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    # With no MCP servers configured the list is empty.
    assert data == []


# ---------------------------------------------------------------------------
# Permission reply
# ---------------------------------------------------------------------------


def test_reply_permission_allow(client: TestClient) -> None:
    resp = client.post(
        "/permission/reply",
        json={"request_id": "fake-id", "decision": "allow"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_reply_permission_deny(client: TestClient) -> None:
    resp = client.post(
        "/permission/reply",
        json={"request_id": "fake-id", "decision": "deny"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_reply_permission_rejects_ask_decision(client: TestClient) -> None:
    resp = client.post(
        "/permission/reply",
        json={"request_id": "fake-id", "decision": "ask"},
    )
    assert resp.status_code == 400


def test_reply_permission_remember_flag(client: TestClient) -> None:
    # remember=True with a project_id and tool should not raise
    resp = client.post(
        "/permission/reply",
        json={
            "request_id": "fake-id",
            "decision": "allow",
            "remember": True,
            "project_id": "proj_abc",
            "tool": "bash",
        },
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /events  and  GET /events/{session_id}
# ---------------------------------------------------------------------------
#
# These endpoints return infinite SSE streams that block forever on an async
# queue.  We patch EventBus.subscribe with an async context manager that
# immediately returns an empty generator so TestClient can complete the
# request and we can assert on status code and content-type.

from contextlib import asynccontextmanager
from unittest.mock import patch as _mock_patch


@asynccontextmanager
async def _empty_bus_subscribe(self):  # type: ignore[override]
    """Finite substitute for EventBus.subscribe() — yields zero events."""

    async def _gen():
        return
        yield  # pragma: no cover  (makes this an async generator)

    yield _gen()


def test_global_events_returns_event_stream(client: TestClient) -> None:
    with _mock_patch("openvibe.bus.EventBus.subscribe", _empty_bus_subscribe):
        resp = client.get("/events")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


def test_session_events_returns_event_stream(client: TestClient) -> None:
    created = _create_session(client)
    with _mock_patch("openvibe.bus.EventBus.subscribe", _empty_bus_subscribe):
        resp = client.get(f"/events/{created['id']}")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


def test_session_events_filtered_to_session_id(client: TestClient) -> None:
    """The per-session stream only yields events whose session_id matches."""
    from dataclasses import dataclass

    created = _create_session(client)
    sid = created["id"]
    other_sid = "other-session"

    @dataclass
    class FakeEvent:
        session_id: str

    emitted: list[FakeEvent] = [
        FakeEvent(session_id=sid),
        FakeEvent(session_id=other_sid),
        FakeEvent(session_id=sid),
    ]

    @asynccontextmanager
    async def _seeded_subscribe(self):  # type: ignore[override]
        async def _gen():
            for ev in emitted:
                yield ev

        yield _gen()

    with _mock_patch("openvibe.bus.EventBus.subscribe", _seeded_subscribe):
        resp = client.get(f"/events/{sid}")

    # Both sid events should be in the response; the other_sid event should not.
    assert resp.status_code == 200
    text = resp.text
    assert text.count(sid) >= 2
    assert other_sid not in text


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def test_list_tools(client: TestClient) -> None:
    resp = client.get("/tool")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    names = {t["name"] for t in data}
    # Default registry must include the standard built-in tools.
    assert "bash" in names
    assert "read" in names
    assert "write" in names
    assert "edit" in names


def test_list_tools_schema_fields(client: TestClient) -> None:
    resp = client.get("/tool")
    for tool in resp.json():
        assert "name" in tool
        assert "description" in tool
        assert "parameters" in tool


# ---------------------------------------------------------------------------
# _serialize_event helper
# ---------------------------------------------------------------------------


def test_serialize_event_dataclass() -> None:
    from dataclasses import dataclass

    @dataclass
    class MyEvent:
        session_id: str
        value: int

    result = _serialize_event(MyEvent(session_id="s1", value=42))
    data = json.loads(result)
    assert data == {"session_id": "s1", "value": 42}


def test_serialize_event_non_dataclass_returns_empty_dict() -> None:
    # Non-dataclasses don't have __dataclass_fields__, so asdict() is skipped
    # and the fallback is an empty dict (the except branch only fires on errors).
    class Plain:
        pass

    result = _serialize_event(Plain())
    data = json.loads(result)
    assert data == {}


def test_serialize_event_returns_valid_json() -> None:
    from dataclasses import dataclass

    @dataclass
    class Evt:
        session_id: str = "abc"

    result = _serialize_event(Evt())
    # Must be parseable JSON.
    json.loads(result)


def test_serialize_event_handles_non_serialisable_field() -> None:
    from dataclasses import dataclass

    @dataclass
    class EvtWithPath:
        session_id: str
        path: Path

    result = _serialize_event(EvtWithPath(session_id="x", path=Path("/tmp")))
    data = json.loads(result)
    # Path is serialised via default=str.
    assert data["path"] == "/tmp"


# ---------------------------------------------------------------------------
# GET /session/{id}/state
# ---------------------------------------------------------------------------


def test_get_session_state_idle(client: TestClient) -> None:
    created = _create_session(client)
    resp = client.get(f"/session/{created['id']}/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "idle"
    assert data["pending_permission"] is None


def test_get_session_state_not_found(client: TestClient) -> None:
    resp = client.get("/session/nonexistent/state")
    assert resp.status_code == 404


def test_get_session_state_interrupted(tmp_path: Path) -> None:
    """A session with an unresolved ToolPart (output=None) reports 'interrupted'."""
    from openvibe.config import ToolStateStatus
    from openvibe.db import create_database
    from openvibe.session import session as session_store
    from openvibe.session.models import ToolPart, ToolState
    from openvibe.config import MessageRole

    db = create_database(tmp_path / "test.db")
    app = create_app(project_dir=tmp_path, config=Config(), db=db, llm=_FakeLLM())

    with TestClient(app) as c:
        created = _create_session(c)
        sid = created["id"]

        # Manually inject an assistant message with an interrupted ToolPart.
        tool_part = ToolPart(
            state=ToolState(
                status=ToolStateStatus.RUNNING,
                call_id="call_abc",
                tool_name="bash",
                input={"command": "ls"},
                output=None,  # interrupted — no output
            )
        )
        session_store.add_message(db, sid, MessageRole.ASSISTANT, [tool_part])

        resp = c.get(f"/session/{sid}/state")

    assert resp.status_code == 200
    assert resp.json()["state"] == "interrupted"


def test_get_session_state_after_send_becomes_idle(client: TestClient) -> None:
    """After a completed send the state reverts to idle."""
    created = _create_session(client)
    client.post(f"/session/{created['id']}/message", json={"text": "hi"})
    resp = client.get(f"/session/{created['id']}/state")
    assert resp.json()["state"] == "idle"


# ---------------------------------------------------------------------------
# POST /session/{id}/abort
# ---------------------------------------------------------------------------


def test_abort_no_active_turn(client: TestClient) -> None:
    created = _create_session(client)
    resp = client.post(f"/session/{created['id']}/abort")
    assert resp.status_code == 200
    assert resp.json() == {"status": "no_active_turn"}


def test_abort_not_found(client: TestClient) -> None:
    resp = client.post("/session/nonexistent/abort")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /session/{id}/resume
# ---------------------------------------------------------------------------


def test_resume_not_found(client: TestClient) -> None:
    resp = client.post("/session/nonexistent/resume", json={"allow": True})
    assert resp.status_code == 404


def test_resume_not_interrupted(client: TestClient) -> None:
    """Resuming a session with no interrupted tool calls returns 400."""
    created = _create_session(client)
    resp = client.post(f"/session/{created['id']}/resume", json={"allow": True})
    assert resp.status_code == 400


def test_resume_allow_streams_sse(tmp_path: Path) -> None:
    """Resuming with allow=True executes the tool and streams the response."""
    from openvibe.config import ToolStateStatus, MessageRole
    from openvibe.db import create_database
    from openvibe.session import session as session_store
    from openvibe.session.models import ToolPart, ToolState, TextPart

    db = create_database(tmp_path / "test.db")
    app = create_app(project_dir=tmp_path, config=Config(), db=db, llm=_FakeLLM())

    with TestClient(app) as c:
        created = _create_session(c)
        sid = created["id"]

        # Seed history: user message + interrupted assistant tool call.
        session_store.add_message(
            db, sid, MessageRole.USER, [TextPart(content="run ls")]
        )
        tool_part = ToolPart(
            state=ToolState(
                status=ToolStateStatus.RUNNING,
                call_id="call_xyz",
                tool_name="bash",
                input={"command": "echo hello"},
                output=None,
            )
        )
        session_store.add_message(db, sid, MessageRole.ASSISTANT, [tool_part])

        resp = c.post(f"/session/{sid}/resume", json={"allow": True})

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    assert b"data:" in resp.content


def test_resume_deny_streams_sse(tmp_path: Path) -> None:
    """Resuming with allow=False injects a denied result and streams the response."""
    from openvibe.config import ToolStateStatus, MessageRole
    from openvibe.db import create_database
    from openvibe.session import session as session_store
    from openvibe.session.models import ToolPart, ToolState, TextPart

    db = create_database(tmp_path / "test.db")
    app = create_app(project_dir=tmp_path, config=Config(), db=db, llm=_FakeLLM())

    with TestClient(app) as c:
        created = _create_session(c)
        sid = created["id"]

        session_store.add_message(
            db, sid, MessageRole.USER, [TextPart(content="run ls")]
        )
        tool_part = ToolPart(
            state=ToolState(
                status=ToolStateStatus.RUNNING,
                call_id="call_deny",
                tool_name="bash",
                input={"command": "echo hello"},
                output=None,
            )
        )
        session_store.add_message(db, sid, MessageRole.ASSISTANT, [tool_part])

        resp = c.post(f"/session/{sid}/resume", json={"allow": False})

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


def test_resume_marks_tool_completed_in_db(tmp_path: Path) -> None:
    """After resume allow=True the interrupted ToolPart should have output in DB."""
    from openvibe.config import ToolStateStatus, MessageRole
    from openvibe.db import create_database
    from openvibe.session import session as session_store
    from openvibe.session.models import ToolPart, ToolState, TextPart

    db = create_database(tmp_path / "test.db")
    app = create_app(project_dir=tmp_path, config=Config(), db=db, llm=_FakeLLM())

    with TestClient(app) as c:
        created = _create_session(c)
        sid = created["id"]

        session_store.add_message(
            db, sid, MessageRole.USER, [TextPart(content="run ls")]
        )
        tool_part = ToolPart(
            state=ToolState(
                status=ToolStateStatus.RUNNING,
                call_id="call_check",
                tool_name="bash",
                input={"command": "echo hello"},
                output=None,
            )
        )
        session_store.add_message(db, sid, MessageRole.ASSISTANT, [tool_part])

        c.post(f"/session/{sid}/resume", json={"allow": True})

        # After resume the state endpoint should no longer report interrupted.
        state_resp = c.get(f"/session/{sid}/state")

    assert state_resp.json()["state"] != "interrupted"


# ---------------------------------------------------------------------------
# _serialize_event helper (continued)
# ---------------------------------------------------------------------------


def test_serialize_event_bus_event() -> None:
    from openvibe.session.models import SessionCreatedEvent

    event = SessionCreatedEvent(session_id="ses_123")
    result = _serialize_event(event)
    data = json.loads(result)
    assert data["session_id"] == "ses_123"
