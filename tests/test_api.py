"""Unit tests for openvibe.api (Session, OpenVibe, _run_turn internals).

All tests inject a MockLLM so no network calls are made.  A temp-file SQLite
database is used for each test so sessions are isolated and the worker thread
can share the same file via thread-local connections.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from openvibe.api import InvalidStateError, OpenVibe, Response, SessionState
from openvibe.config import AgentConfig, Config

from tests.mock_llm import MockLLM, ScriptedMockLLM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ov(tmp_path: Path, llm=None, config: Config | None = None) -> OpenVibe:
    """Create an OpenVibe wired with a temp DB and optional mock LLM."""
    from openvibe.db import create_database

    db = create_database(tmp_path / "test.db")
    return OpenVibe(
        project_dir=tmp_path,
        db=db,
        config=config or Config(),
        llm=llm,
    )


def _messages_after_send(tmp_path: Path, user_text: str = "foo", reply: str = "bar"):
    """Return the stored MessageInfo list after one send."""
    llm = MockLLM({user_text: reply})
    with _ov(tmp_path, llm=llm) as ov:
        s = ov.create_session()
        s.send(user_text)
        return s.messages()


# ---------------------------------------------------------------------------
# OpenVibe lifecycle
# ---------------------------------------------------------------------------

def test_context_manager_starts_and_closes(tmp_path):
    with _ov(tmp_path) as ov:
        assert ov._db is not None
        assert ov._registry is not None
        assert ov._project is not None
    assert ov._db is None


def test_start_idempotent_db(tmp_path):
    """Passing db= should skip create_database inside start()."""
    from openvibe.db import create_database

    db = create_database(tmp_path / "test.db")
    ov = OpenVibe(project_dir=tmp_path, db=db, config=Config())
    ov.start()
    assert ov._db is db
    ov.close()


def test_require_started_raises_before_start(tmp_path):
    # Do NOT inject db — that would make _require_started() pass
    ov = OpenVibe(project_dir=tmp_path, config=Config())
    with pytest.raises(RuntimeError, match="not started"):
        ov.list_sessions()


def test_close_is_idempotent(tmp_path):
    ov = _ov(tmp_path)
    ov.start()
    ov.close()
    ov.close()  # second close must not raise


# ---------------------------------------------------------------------------
# Session management (CRUD)
# ---------------------------------------------------------------------------

def test_create_session(tmp_path):
    with _ov(tmp_path) as ov:
        session = ov.create_session()
        assert session.id.startswith("ses_")
        assert session.state == SessionState.IDLE


def test_list_sessions_empty(tmp_path):
    with _ov(tmp_path) as ov:
        assert ov.list_sessions() == []


def test_list_sessions_returns_created(tmp_path):
    with _ov(tmp_path) as ov:
        s1 = ov.create_session(title="first")
        s2 = ov.create_session(title="second")
        ids = {s.id for s in ov.list_sessions()}
        assert s1.id in ids
        assert s2.id in ids


def test_get_session_found(tmp_path):
    with _ov(tmp_path) as ov:
        created = ov.create_session(title="hello")
        fetched = ov.get_session(created.id)
        assert fetched.id == created.id


def test_get_session_missing_raises(tmp_path):
    with _ov(tmp_path) as ov:
        with pytest.raises(KeyError):
            ov.get_session("ses_doesnotexist")


def test_delete_session_removes_from_listing(tmp_path):
    with _ov(tmp_path) as ov:
        session = ov.create_session()
        ov.delete_session(session.id)
        ids = [s.id for s in ov.list_sessions()]
        assert session.id not in ids


def test_session_info_properties(tmp_path):
    with _ov(tmp_path) as ov:
        session = ov.create_session(title="my session")
        assert session.info.title == "my session"
        assert session.info.project_id == ov._project.id


def test_different_agents(tmp_path):
    with _ov(tmp_path) as ov:
        build_s = ov.create_session(agent="build")
        plan_s = ov.create_session(agent="plan")
        assert build_s._agent_name == "build"
        assert plan_s._agent_name == "plan"


# ---------------------------------------------------------------------------
# Session.send() — simple text turns
# ---------------------------------------------------------------------------

def test_send_returns_idle_state(tmp_path):
    llm = MockLLM({"ping": "pong"})
    with _ov(tmp_path, llm=llm) as ov:
        response = ov.create_session().send("ping")
        assert response.state == SessionState.IDLE


def test_send_returns_correct_text(tmp_path):
    llm = MockLLM({"hello": "world"})
    with _ov(tmp_path, llm=llm) as ov:
        response = ov.create_session().send("hello")
        assert response.text == "world"


def test_send_fallback_text(tmp_path):
    llm = MockLLM({}, fallback="fallback reply")
    with _ov(tmp_path, llm=llm) as ov:
        response = ov.create_session().send("anything")
        assert response.text == "fallback reply"


def test_send_response_contains_messages(tmp_path):
    llm = MockLLM({"q": "a"})
    with _ov(tmp_path, llm=llm) as ov:
        response = ov.create_session().send("q")
        assert len(response.messages) >= 2


def test_send_on_token_streams_text(tmp_path):
    llm = MockLLM({"stream me": "streamed content"})
    with _ov(tmp_path, llm=llm) as ov:
        tokens: list[str] = []
        response = ov.create_session().send("stream me", on_token=tokens.append)
        assert response.state == SessionState.IDLE
        assert "".join(tokens) == "streamed content"


def test_send_on_token_called_before_return(tmp_path):
    """on_token must fire before send() returns."""
    llm = MockLLM({"go": "token data"})
    with _ov(tmp_path, llm=llm) as ov:
        received: list[str] = []
        ov.create_session().send("go", on_token=received.append)
        assert "".join(received) == "token data"


# ---------------------------------------------------------------------------
# Session.messages() — history persistence
# ---------------------------------------------------------------------------

def test_messages_stored_after_send(tmp_path):
    llm = MockLLM({"hi": "hello"})
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session()
        session.send("hi")
        roles = [m.role for m in session.messages()]
        assert "user" in roles
        assert "assistant" in roles


def test_multi_turn_history_grows(tmp_path):
    llm = MockLLM({"turn1": "reply1", "turn2": "reply2"})
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session()
        session.send("turn1")
        count_after_first = len(session.messages())
        session.send("turn2")
        assert len(session.messages()) > count_after_first


def test_multi_turn_responses_are_correct(tmp_path):
    llm = MockLLM({"first": "alpha", "second": "beta"})
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session()
        assert session.send("first").text == "alpha"
        assert session.send("second").text == "beta"


# ---------------------------------------------------------------------------
# FSM state enforcement
# ---------------------------------------------------------------------------

def test_send_in_thinking_state_raises(tmp_path):
    llm = MockLLM({"x": "y"})
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session()
        session._state = SessionState.THINKING
        with pytest.raises(InvalidStateError, match="IDLE"):
            session.send("x")
        session._state = SessionState.IDLE


def test_reply_in_idle_state_raises(tmp_path):
    with _ov(tmp_path) as ov:
        session = ov.create_session()
        with pytest.raises(InvalidStateError, match="WAITING"):
            session.reply("fake-id", "allow")


def test_send_nowait_in_thinking_state_raises(tmp_path):
    with _ov(tmp_path) as ov:
        session = ov.create_session()
        session._state = SessionState.THINKING
        with pytest.raises(InvalidStateError):
            session.send_nowait("x")
        session._state = SessionState.IDLE


# ---------------------------------------------------------------------------
# Non-blocking API
# ---------------------------------------------------------------------------

def test_send_nowait_returns_immediately(tmp_path):
    started = threading.Event()
    original_llm = MockLLM({"q": "a"})

    def slow_llm(model, messages, **kw):
        started.set()
        time.sleep(0.05)
        return original_llm(model, messages, **kw)

    with _ov(tmp_path, llm=slow_llm) as ov:
        session = ov.create_session()
        t_before = time.monotonic()
        session.send_nowait("q")
        t_after = time.monotonic()
        assert (t_after - t_before) < 0.04  # must return before the 50ms sleep
        started.wait(timeout=2)
        time.sleep(0.15)
        if not session._result_q.empty():
            session._result_q.get_nowait()
        session._state = SessionState.IDLE


def test_send_nowait_callback_invoked(tmp_path):
    llm = MockLLM({"cb": "done"})
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session()
        received: list[Response] = []
        ev = threading.Event()

        def handle(resp: Response) -> None:
            received.append(resp)
            ev.set()

        session.send_nowait("cb", callback=handle)
        ev.wait(timeout=5)
        assert len(received) == 1
        assert received[0].state == SessionState.IDLE
        assert received[0].text == "done"


def test_send_nowait_callback_tokens(tmp_path):
    llm = MockLLM({"tok": "token_text"})
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session()
        tokens: list[str] = []
        ev = threading.Event()

        session.send_nowait("tok", callback=lambda _: ev.set(), on_token=tokens.append)
        ev.wait(timeout=5)
        assert "".join(tokens) == "token_text"


# ---------------------------------------------------------------------------
# Permission flow (WAITING → reply)
# ---------------------------------------------------------------------------

def test_permission_ask_triggers_waiting(tmp_path):
    """Bash tool is ASK in the build agent — must return WAITING."""
    llm = ScriptedMockLLM([("bash", {"command": "echo hello"}), "done"])
    with _ov(tmp_path, llm=llm) as ov:
        response = ov.create_session(agent="build").send("run a command")
        assert response.state == SessionState.WAITING
        assert response.request is not None
        assert response.request.kind == "permission"


def test_permission_allow_resumes_to_idle(tmp_path):
    """After allow, the agent finishes and returns IDLE."""
    llm = ScriptedMockLLM([("bash", {"command": "echo hello"}), "done"])
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session(agent="build")
        waiting = session.send("run a command")
        assert waiting.state == SessionState.WAITING
        done = session.reply(waiting.request.id, "allow")
        assert done.state == SessionState.IDLE
        assert done.text == "done"


def test_permission_deny_returns_idle(tmp_path):
    """Denying permission still completes the turn with the final LLM text."""
    llm = ScriptedMockLLM([("bash", {"command": "echo hi"}), "after deny"])
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session(agent="build")
        waiting = session.send("do it")
        assert waiting.state == SessionState.WAITING
        done = session.reply(waiting.request.id, "deny")
        assert done.state == SessionState.IDLE
        assert done.text == "after deny"


def test_permission_request_has_options(tmp_path):
    """InputRequest must expose allow, allow_always, and deny options."""
    llm = ScriptedMockLLM([("bash", {"command": "ls"}), "ok"])
    with _ov(tmp_path, llm=llm) as ov:
        resp = ov.create_session(agent="build").send("list files")
        assert resp.state == SessionState.WAITING
        option_values = {o.value for o in resp.request.options}
        assert "allow" in option_values
        assert "allow_always" in option_values
        assert "deny" in option_values


# ---------------------------------------------------------------------------
# Abort
# ---------------------------------------------------------------------------

def test_abort_resets_to_idle(tmp_path):
    with _ov(tmp_path) as ov:
        session = ov.create_session()
        session._state = SessionState.THINKING
        session.abort(timeout=1.0)
        assert session.state == SessionState.IDLE


def test_abort_during_send_nowait(tmp_path):
    """Abort an actually-running background worker."""
    started = threading.Event()

    def slow_llm(model, messages, **kw):
        started.set()
        time.sleep(10)
        return MockLLM({}).responses  # unreachable

    with _ov(tmp_path, llm=slow_llm) as ov:
        session = ov.create_session()
        session.send_nowait("go")
        started.wait(timeout=3)
        session.abort(timeout=2.0)
        assert session.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# run() one-shot convenience
# ---------------------------------------------------------------------------

def test_run_returns_idle_response(tmp_path):
    llm = MockLLM({"what is up": "not much"})
    with _ov(tmp_path, llm=llm) as ov:
        resp = ov.run("what is up")
        assert resp.state == SessionState.IDLE
        assert resp.text == "not much"


def test_run_on_token_callback(tmp_path):
    llm = MockLLM({"stream": "streamed"})
    with _ov(tmp_path, llm=llm) as ov:
        tokens: list[str] = []
        resp = ov.run("stream", on_token=tokens.append)
        assert resp.state == SessionState.IDLE
        assert "".join(tokens) == "streamed"


def test_run_auto_allow_permissions(tmp_path):
    """run(on_permission='allow') must auto-approve bash calls."""
    llm = ScriptedMockLLM([("bash", {"command": "echo auto"}), "auto done"])
    with _ov(tmp_path, llm=llm) as ov:
        resp = ov.run("do it", on_permission="allow")
        assert resp.state == SessionState.IDLE
        assert resp.text == "auto done"


def test_run_auto_deny_permissions(tmp_path):
    """run(on_permission='deny') must auto-deny bash calls and finish."""
    llm = ScriptedMockLLM([("bash", {"command": "echo deny"}), "after deny"])
    with _ov(tmp_path, llm=llm) as ov:
        assert ov.run("do it", on_permission="deny").state == SessionState.IDLE


def test_run_ask_permission_raises(tmp_path):
    """run(on_permission='ask') must raise when a permission is needed."""
    llm = ScriptedMockLLM([("bash", {"command": "echo raise"}), "never reached"])
    with _ov(tmp_path, llm=llm) as ov:
        with pytest.raises(RuntimeError, match="on_permission='ask'"):
            ov.run("do it", on_permission="ask")


def test_run_creates_isolated_session(tmp_path):
    """Each run() call creates a separate session."""
    llm = MockLLM({"q": "r"})
    with _ov(tmp_path, llm=llm) as ov:
        ov.run("q")
        ov.run("q")
        assert len(ov.list_sessions()) == 2


# ---------------------------------------------------------------------------
# Doom-loop protection
# ---------------------------------------------------------------------------

def test_doom_loop_guard_fires(tmp_path):
    """The same tool+args repeated >= 3 times must be rejected."""
    config = Config(agent={"build": AgentConfig(max_steps=5)})
    llm = ScriptedMockLLM([("bash", {"command": "rm -rf /"})])

    from openvibe.session.models import ToolPart

    with _ov(tmp_path, llm=llm, config=config) as ov:
        resp = ov.run("do the thing", on_permission="deny")
        assert resp.state == SessionState.IDLE

        session = ov.get_session(resp.messages[-1].session_id)
        all_parts = [
            p
            for m in session.messages()
            for p in m.parts
            if isinstance(p, ToolPart)
        ]
        doom_parts = [p for p in all_parts if p.state.output and "Doom loop" in p.state.output]
        assert len(doom_parts) >= 1


# ---------------------------------------------------------------------------
# Error classification (_classify_error)
# ---------------------------------------------------------------------------

def test_classify_auth_error():
    from openvibe.api import _classify_error
    kind, _ = _classify_error(Exception("Invalid API key provided"))
    assert kind == "auth"


def test_classify_unauthorized_error():
    from openvibe.api import _classify_error
    kind, _ = _classify_error(Exception("401 Unauthorized"))
    assert kind == "auth"


def test_classify_context_overflow_error():
    from openvibe.api import _classify_error
    kind, _ = _classify_error(Exception("context length exceeded limit"))
    assert kind == "context_overflow"


def test_classify_context_window_error():
    from openvibe.api import _classify_error
    kind, _ = _classify_error(Exception("context window too large"))
    assert kind == "context_overflow"


def test_classify_generic_api_error():
    from openvibe.api import _classify_error
    kind, _ = _classify_error(Exception("something went wrong"))
    assert kind == "api_error"


def test_classify_error_returns_original_message():
    from openvibe.api import _classify_error
    _, msg = _classify_error(Exception("original message text"))
    assert "original message text" in msg


# ---------------------------------------------------------------------------
# _messages_to_litellm conversion
# ---------------------------------------------------------------------------

def test_messages_to_litellm_user_role(tmp_path):
    from openvibe.api import _messages_to_litellm
    msgs = _messages_after_send(tmp_path, "foo", "bar")
    roles = [m["role"] for m in _messages_to_litellm(msgs)]
    assert "user" in roles


def test_messages_to_litellm_assistant_role(tmp_path):
    from openvibe.api import _messages_to_litellm
    msgs = _messages_after_send(tmp_path, "foo", "bar")
    roles = [m["role"] for m in _messages_to_litellm(msgs)]
    assert "assistant" in roles


def test_messages_to_litellm_text_preserved(tmp_path):
    from openvibe.api import _messages_to_litellm
    msgs = _messages_after_send(tmp_path, "foo", "bar")
    ll = _messages_to_litellm(msgs)
    user_msg = next(m for m in ll if m["role"] == "user")
    assert "foo" in user_msg["content"]


# ---------------------------------------------------------------------------
# get_session returns a working Session
# ---------------------------------------------------------------------------

def test_get_session_can_continue_conversation(tmp_path):
    llm = MockLLM({"start": "begun", "continue": "continued"})
    with _ov(tmp_path, llm=llm) as ov:
        s1 = ov.create_session()
        s1.send("start")

        s2 = ov.get_session(s1.id)
        resp = s2.send("continue")
        assert resp.text == "continued"
        assert len(s2.messages()) >= 4  # user+asst × 2


# ---------------------------------------------------------------------------
# reply_nowait
# ---------------------------------------------------------------------------

def test_reply_nowait_in_idle_raises(tmp_path):
    with _ov(tmp_path) as ov:
        session = ov.create_session()
        with pytest.raises(InvalidStateError, match="WAITING"):
            session.reply_nowait("fake-id", "allow")


def test_reply_nowait_delivers_response(tmp_path):
    """reply_nowait() must deliver the final response via its callback."""
    llm = ScriptedMockLLM([("bash", {"command": "echo hi"}), "done"])
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session(agent="build")
        waiting = session.send("run it")
        assert waiting.state == SessionState.WAITING

        received: list[Response] = []
        ev = threading.Event()

        def handle(resp: Response) -> None:
            received.append(resp)
            ev.set()

        session.reply_nowait(waiting.request.id, "allow", callback=handle)
        ev.wait(timeout=5)
        assert len(received) == 1
        assert received[0].state == SessionState.IDLE


def test_reply_nowait_without_callback_still_transitions(tmp_path):
    """reply_nowait() without a callback must still let the worker finish."""
    llm = ScriptedMockLLM([("bash", {"command": "echo hi"}), "done"])
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session(agent="build")
        waiting = session.send("run it")
        assert waiting.state == SessionState.WAITING

        session.reply_nowait(waiting.request.id, "allow")
        # No callback — poll until idle or timeout
        deadline = time.monotonic() + 5.0
        while session.state == SessionState.THINKING and time.monotonic() < deadline:
            time.sleep(0.05)
        # Drain result queue so the session stays consistent
        if not session._result_q.empty():
            r = session._result_q.get_nowait()
            session._state = r.state
        assert session.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# on_message and on_tool callbacks
# ---------------------------------------------------------------------------

def test_send_on_message_accepted_without_error(tmp_path):
    """on_message is stored but not called in the sync worker path — must not crash."""
    llm = MockLLM({"hello": "world"})
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session()
        events: list[tuple[str, str]] = []
        resp = session.send("hello", on_message=lambda msg_id, role: events.append((msg_id, role)))
        # Sync path does not fire on_message — just verify no exception and correct state.
        assert resp.state == SessionState.IDLE
        assert resp.text == "world"


def test_send_on_tool_accepted_without_error(tmp_path):
    """on_tool is stored but not called in the sync worker path — must not crash."""
    llm = ScriptedMockLLM([("bash", {"command": "echo hi"}), "done"])
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session(agent="build")
        tool_events: list[tuple] = []
        resp = session.send(
            "run it",
            on_tool=lambda msg_id, idx, state: tool_events.append((msg_id, idx, state)),
        )
        assert resp.state in (SessionState.WAITING, SessionState.IDLE)


# ---------------------------------------------------------------------------
# ERROR state and recovery
# ---------------------------------------------------------------------------

def test_send_error_response_on_llm_exception(tmp_path):
    def boom(model, messages, **kw):
        raise RuntimeError("something went wrong")

    with _ov(tmp_path, llm=boom) as ov:
        resp = ov.create_session().send("anything")
        assert resp.state == SessionState.ERROR
        assert resp.error is not None
        assert resp.error.kind == "api_error"


def test_error_response_message_in_error_info(tmp_path):
    def boom(model, messages, **kw):
        raise RuntimeError("something went wrong")

    with _ov(tmp_path, llm=boom) as ov:
        resp = ov.create_session().send("anything")
        assert "something went wrong" in resp.error.message


def test_auth_error_classified_correctly(tmp_path):
    def boom(model, messages, **kw):
        raise RuntimeError("Invalid API key provided")

    with _ov(tmp_path, llm=boom) as ov:
        resp = ov.create_session().send("anything")
        assert resp.state == SessionState.ERROR
        assert resp.error.kind == "auth"


# ---------------------------------------------------------------------------
# delete_session archives (hides from listing, does not hard-delete)
# ---------------------------------------------------------------------------

def test_delete_session_archives_not_hard_deletes(tmp_path):
    """delete_session() archives so the session is hidden from list_sessions()
    but still retrievable by ID."""
    with _ov(tmp_path) as ov:
        session = ov.create_session()
        sid = session.id
        ov.delete_session(sid)

        ids = [s.id for s in ov.list_sessions()]
        assert sid not in ids

        # Still retrievable
        fetched = ov.get_session(sid)
        assert fetched.id == sid


# ---------------------------------------------------------------------------
# _build_system_prompt
# ---------------------------------------------------------------------------

def test_build_system_prompt_with_base_only():
    from openvibe.api import _build_system_prompt
    from openvibe.agent.agent import AgentInfo

    agent = AgentInfo(name="x", description="", system_prompt="base prompt")
    assert _build_system_prompt(agent) == "base prompt"


def test_build_system_prompt_with_extra_instructions():
    from openvibe.api import _build_system_prompt
    from openvibe.agent.agent import AgentInfo

    agent = AgentInfo(
        name="x",
        description="",
        system_prompt="base",
        extra_instructions=["extra1", "extra2"],
    )
    result = _build_system_prompt(agent)
    assert "base" in result
    assert "extra1" in result
    assert "extra2" in result


def test_build_system_prompt_empty_agent():
    from openvibe.api import _build_system_prompt
    from openvibe.agent.agent import AgentInfo

    agent = AgentInfo(name="x", description="", system_prompt="")
    assert _build_system_prompt(agent) == ""


# ---------------------------------------------------------------------------
# _model_string
# ---------------------------------------------------------------------------

def test_model_string_default():
    from openvibe.api import _model_string
    from openvibe.agent.agent import AgentInfo

    agent = AgentInfo(name="x", description="", system_prompt="")
    assert _model_string(agent) == "anthropic/claude-sonnet-4-5"


def test_model_string_with_model():
    from openvibe.api import _model_string
    from openvibe.agent.agent import AgentInfo
    from openvibe.config import ModelRef

    agent = AgentInfo(
        name="x",
        description="",
        system_prompt="",
        model=ModelRef(provider_id="openai", model_id="gpt-4o"),
    )
    assert _model_string(agent) == "openai/gpt-4o"


# ---------------------------------------------------------------------------
# _messages_to_litellm — tool call serialisation
# ---------------------------------------------------------------------------

def test_messages_to_litellm_with_tool_call(tmp_path):
    """An assistant message with a ToolPart must produce tool_calls + tool result."""
    from openvibe.api import _messages_to_litellm
    from openvibe.config import MessageRole, ToolStateStatus
    from openvibe.session.models import ToolPart, ToolState

    llm = ScriptedMockLLM([("bash", {"command": "echo hi"}), "done"])
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session(agent="build")
        resp = session.send("run it", )
        # auto-allow so we get a complete turn
        if resp.state == SessionState.WAITING:
            resp = session.reply(resp.request.id, "allow")

        ll = _messages_to_litellm(session.messages())
        roles = [m["role"] for m in ll]
        assert "tool" in roles
        asst = next(m for m in ll if m["role"] == "assistant" and m.get("tool_calls"))
        assert asst["tool_calls"][0]["function"]["name"] == "bash"


def test_messages_to_litellm_skips_empty_assistant_messages(tmp_path):
    """Assistant messages with no text and no tool calls must be omitted."""
    from openvibe.api import _messages_to_litellm
    from openvibe.session import session as _session_store
    from openvibe.config import MessageRole

    llm = MockLLM({"hi": "hello"})
    with _ov(tmp_path, llm=llm) as ov:
        s = ov.create_session()
        s.send("hi")
        msgs = s.messages()
        # Manually add an empty assistant message
        _session_store.add_message(ov._db, s.id, MessageRole.ASSISTANT)
        all_msgs = s.messages()
        ll = _messages_to_litellm(all_msgs)
        # The empty assistant message must be skipped
        empty = [m for m in ll if m["role"] == "assistant" and not m.get("content") and not m.get("tool_calls")]
        assert not empty


# ---------------------------------------------------------------------------
# run() with custom agent
# ---------------------------------------------------------------------------

def test_run_with_plan_agent(tmp_path):
    llm = MockLLM({"analyse this": "analysis done"})
    with _ov(tmp_path, llm=llm) as ov:
        resp = ov.run("analyse this", agent="plan")
        assert resp.state == SessionState.IDLE
        assert resp.text == "analysis done"


# ---------------------------------------------------------------------------
# FSM edge cases
# ---------------------------------------------------------------------------

def test_send_after_error_state_raises(tmp_path):
    """After the session enters ERROR, send() must raise InvalidStateError."""
    def boom(model, messages, **kw):
        raise RuntimeError("boom")

    with _ov(tmp_path, llm=boom) as ov:
        session = ov.create_session()
        resp = session.send("anything")
        assert resp.state == SessionState.ERROR
        # Session is now in ERROR state — send() must reject it
        with pytest.raises(InvalidStateError):
            session.send("try again")


def test_reply_in_waiting_with_wrong_request_id_still_resumes(tmp_path):
    """The sync worker ignores the request_id value and uses only the option."""
    llm = ScriptedMockLLM([("bash", {"command": "echo hi"}), "done"])
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session(agent="build")
        waiting = session.send("run it")
        assert waiting.state == SessionState.WAITING
        # Passing a completely wrong request_id — worker must still resume
        done = session.reply("wrong-request-id-entirely", "allow")
        assert done.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# allow_always permission option
# ---------------------------------------------------------------------------

def test_permission_allow_always_resumes_to_idle(tmp_path):
    """reply with 'allow_always' must behave like 'allow' in the sync path."""
    llm = ScriptedMockLLM([("bash", {"command": "echo hi"}), "done"])
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session(agent="build")
        waiting = session.send("run it")
        assert waiting.state == SessionState.WAITING
        done = session.reply(waiting.request.id, "allow_always")
        assert done.state == SessionState.IDLE
        assert done.text == "done"


# ---------------------------------------------------------------------------
# _messages_to_litellm — tool without output
# ---------------------------------------------------------------------------

def test_messages_to_litellm_tool_without_output(tmp_path):
    """ToolPart with output=None must not emit a tool result row."""
    from openvibe.api import _messages_to_litellm
    from openvibe.config import MessageRole, ToolStateStatus
    from openvibe.session import session as _session_store
    from openvibe.session.models import ToolPart, ToolState

    with _ov(tmp_path) as ov:
        s = ov.create_session()
        # Create an assistant message with a ToolPart that has no output yet
        msg = _session_store.add_message(ov._db, s.id, MessageRole.ASSISTANT)
        _session_store.upsert_part(
            ov._db, msg.id, 0,
            ToolPart(state=ToolState(
                status=ToolStateStatus.RUNNING,
                call_id="call_x",
                tool_name="bash",
                input={"command": "ls"},
                output=None,  # no output yet
            ))
        )
        ll = _messages_to_litellm(s.messages())
        tool_result_rows = [m for m in ll if m.get("role") == "tool"]
        assert tool_result_rows == []


# ---------------------------------------------------------------------------
# Session message isolation
# ---------------------------------------------------------------------------

def test_sessions_have_isolated_message_history(tmp_path):
    """Messages from session A must not appear in session B."""
    llm = MockLLM({"a": "reply_a", "b": "reply_b"})
    with _ov(tmp_path, llm=llm) as ov:
        sa = ov.create_session()
        sb = ov.create_session()
        sa.send("a")
        sb.send("b")
        contents_a = [
            p.content
            for m in sa.messages()
            for p in m.parts
            if hasattr(p, "content")
        ]
        contents_b = [
            p.content
            for m in sb.messages()
            for p in m.parts
            if hasattr(p, "content")
        ]
        assert "reply_a" in contents_a
        assert "reply_a" not in contents_b
        assert "reply_b" in contents_b
        assert "reply_b" not in contents_a


# ---------------------------------------------------------------------------
# project_dir property
# ---------------------------------------------------------------------------

def test_project_dir_property(tmp_path):
    with _ov(tmp_path) as ov:
        assert ov.project_dir == tmp_path.resolve()
