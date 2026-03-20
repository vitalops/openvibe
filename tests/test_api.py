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
    """Denying permission still completes the turn."""
    llm = ScriptedMockLLM([("bash", {"command": "echo hi"}), "after deny"])
    with _ov(tmp_path, llm=llm) as ov:
        session = ov.create_session(agent="build")
        waiting = session.send("do it")
        assert waiting.state == SessionState.WAITING
        assert session.reply(waiting.request.id, "deny").state == SessionState.IDLE


def test_permission_request_has_options(tmp_path):
    """InputRequest must expose allow/deny options."""
    llm = ScriptedMockLLM([("bash", {"command": "ls"}), "ok"])
    with _ov(tmp_path, llm=llm) as ov:
        resp = ov.create_session(agent="build").send("list files")
        assert resp.state == SessionState.WAITING
        option_values = {o.value for o in resp.request.options}
        assert "allow" in option_values
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

def test_classify_auth_error(tmp_path):
    from openvibe.api import _classify_error
    kind, _ = _classify_error(Exception("Invalid API key provided"))
    assert kind == "auth"


def test_classify_context_overflow_error(tmp_path):
    from openvibe.api import _classify_error
    kind, _ = _classify_error(Exception("context length exceeded limit"))
    assert kind == "context_overflow"


def test_classify_generic_api_error(tmp_path):
    from openvibe.api import _classify_error
    kind, _ = _classify_error(Exception("something went wrong"))
    assert kind == "api_error"


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
