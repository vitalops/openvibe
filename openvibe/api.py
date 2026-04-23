"""openvibe public API — synchronous, FSM-based.

The entire public contract lives here.  No async/await anywhere in this
module; all async internals (litellm, MCP, tools) are either converted to
sync or run on a contained event loop inside the worker thread.

Quick-start::

    from openvibe import OpenVibe

    with OpenVibe() as ov:
        session = ov.create_session()

        response = session.send(
            "refactor main.py",
            on_token=lambda t: print(t, end="", flush=True),
        )

        while response.state == SessionState.WAITING:
            req = response.request
            print(f"\\n{req.description}")
            for opt in req.options:
                print(f"  [{opt.value}] {opt.label}")
            choice = input("choice: ").strip()
            response = session.reply(req.id, choice)

        if response.state == SessionState.ERROR:
            print("error:", response.error.message)

One-shot headless::

    with OpenVibe() as ov:
        result = ov.run("what does this repo do?", on_token=print)
        # result.text has the full response; already printed token-by-token above

Non-blocking with callback (e.g. for a GUI/TUI)::

    def handle(response):
        if response.state == SessionState.WAITING:
            session.reply_nowait(response.request.id, "allow", callback=handle)
        elif response.state == SessionState.IDLE:
            update_ui(response.text)

    session.send_nowait("fix the tests", callback=handle, on_token=stream_to_ui)
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_DOOM_THRESHOLD = 3
_PERMISSION_TIMEOUT = 300.0  # seconds a worker waits for a permission reply


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class SessionState(StrEnum):
    IDLE = "idle"
    THINKING = "thinking"
    WAITING = "waiting"  # blocked on caller input (e.g. permission request)
    ERROR = "error"


@dataclass
class Option:
    """One selectable choice presented inside an InputRequest."""

    value: str
    label: str


@dataclass
class InputRequest:
    """Emitted when the agent needs a decision from the caller."""

    id: str
    kind: str  # "permission" | "question" | …
    description: str
    tool: str | None = None  # tool name for permission requests
    argument: str | None = None  # raw value being acted on (command, path, …)
    options: list[Option] = field(default_factory=list)


@dataclass
class ErrorInfo:
    kind: str  # "auth" | "context_overflow" | "api_error" | "internal"
    message: str


@dataclass
class Response:
    """Return value of send() and reply().

    Always inspect ``.state`` first:

    * ``IDLE``    — turn finished; ``.text`` has the assistant's full reply.
    * ``WAITING`` — agent needs input; handle ``.request`` then call reply().
    * ``ERROR``   — something went wrong; inspect ``.error``.

    If ``.command_result`` is set, this was a slash command (not an LLM turn).
    """

    state: SessionState
    text: str = ""
    messages: list[Any] = field(default_factory=list)  # list[MessageInfo]
    request: InputRequest | None = None
    error: ErrorInfo | None = None
    command_result: Any = None  # CommandResult when a slash command was executed


class InvalidStateError(Exception):
    """Raised when a Session method is called in the wrong FSM state."""


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class Session:
    """A single conversation thread with explicit FSM state.

    State transitions::

        IDLE ──send()──► THINKING ──permission needed──► WAITING
          ▲                  │                               │
          │            turn complete               reply() / reply_nowait()
          │                  │                               │
          └──────────────────┴───────────────────────────────┘
                          ERROR  (recoverable — next send() resets to THINKING)

    All blocking methods (send, reply) return a Response.
    All non-blocking methods (send_nowait, reply_nowait) return None immediately;
    results are delivered via an optional callback or by polling .state.
    """

    def __init__(
        self,
        session_info: Any,  # openvibe.session.models.SessionInfo
        db: Any,  # openvibe.db.Database
        registry: Any,  # openvibe.tool.base.ToolRegistry
        config: Any,  # openvibe.config.Config
        agent_name: str,
        llm: Any = None,  # sync callable(model, messages, **kw) → iterable[chunk]
        processor: Any = None,  # openvibe.session.processor.SessionProcessor (async path)
        bus: Any = None,  # openvibe.bus.EventBus (async path)
        permissions: Any = None,  # openvibe.permission.permission.PermissionService (async path)
    ) -> None:
        self._info = session_info
        self._db = db
        self._registry = registry
        self._base_config = config
        self._config = self._build_session_config(config, session_info)
        self._agent_name = agent_name
        self._llm = llm
        self._processor = processor
        self._bus = bus
        self._permissions = permissions
        self._state = SessionState.IDLE
        self._lock = threading.Lock()

        # Stored callbacks — set by send/send_nowait, reused by reply/reply_nowait
        self._on_message: Callable[[str, str], None] | None = None
        self._on_tool: Callable[[str, int, Any], None] | None = None

        # Worker ↔ caller communication channels
        # result_q: worker → caller (one Response per pause or completion)
        # resume_q: caller → worker ((request_id, option) to unblock a WAITING worker)
        self._result_q: queue.Queue[Response] = queue.Queue(maxsize=1)
        self._resume_q: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=1)
        self._abort_ev = threading.Event()
        self._worker: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Introspection (always safe to call from any thread)
    # ------------------------------------------------------------------

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def id(self) -> str:
        return self._info.id

    @property
    def info(self) -> Any:  # SessionInfo
        return self._info

    def messages(self) -> list[Any]:  # list[MessageInfo]
        from openvibe.session import session as _store

        return _store.list_messages(self._db, self._info.id)

    # ------------------------------------------------------------------
    # Session-level config
    # ------------------------------------------------------------------

    @staticmethod
    def _build_session_config(base_config: Any, session_info: Any) -> Any:
        """Return a Config that merges base + session-level overrides."""
        import copy
        import json as _json

        from openvibe.config import Config

        overrides_str = getattr(session_info, "config_json", None)
        if not overrides_str:
            return copy.deepcopy(base_config)

        base_dict = base_config.model_dump()
        try:
            overlay = _json.loads(overrides_str)
        except (ValueError, TypeError):
            return copy.deepcopy(base_config)

        from openvibe.config import _deep_merge

        merged = _deep_merge(base_dict, overlay)
        return Config.model_validate(merged)

    def update_session_config(self, overrides: dict[str, Any]) -> None:
        """Persist session-level config *overrides* to the DB and refresh.

        *overrides* is a partial config dict (e.g. ``{"model": {...}}``).
        It is deep-merged with any existing session overrides.
        """
        import json as _json

        from openvibe.config import Config, _deep_merge
        from openvibe.session import session as _store

        existing: dict[str, Any] = {}
        if self._info.config_json:
            try:
                existing = _json.loads(self._info.config_json)
            except (ValueError, TypeError):
                pass

        merged = _deep_merge(existing, overrides)
        blob = _json.dumps(merged)
        _store.update_config(self._db, self._info.id, blob)
        self._info.config_json = blob

        # Rebuild the effective config
        self._config = self._build_session_config(self._base_config, self._info)

    # ------------------------------------------------------------------
    # Blocking API
    # ------------------------------------------------------------------

    def _try_command(self, text: str) -> Response | None:
        """If *text* is a registered slash command, execute it and return a Response.

        Returns ``None`` for unrecognised names so that ``_try_skill`` can
        handle skill invocations before we fall through to the LLM.
        """
        from openvibe.commands import CommandContext, _COMMANDS, execute, get_command, is_command  # noqa: PLC2701

        if not is_command(text):
            return None
        parsed = get_command(text)
        if parsed is None:
            return None
        name, args = parsed
        # Only handle names that are registered as slash commands; unknown
        # names may be skill invocations — let _try_skill decide.
        if name not in _COMMANDS:
            return None
        ctx = CommandContext(session=self, args=args)
        result = execute(name, ctx)
        return Response(
            state=SessionState.IDLE,
            text=result.output,
            command_result=result,
        )

    def _try_skill(self, text: str) -> str | None:
        """If *text* is a skill invocation (``/name args``), return the expanded prompt.

        Returns ``None`` when the text is not a skill invocation so that the
        caller can fall through to the normal LLM path.
        """
        from openvibe.commands import is_command
        from openvibe.skill.registry import get_registry

        if not is_command(text):
            return None
        parts = text[1:].split(None, 1)
        name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        skill = get_registry().get(name)
        if skill is None:
            return None
        return skill.get_prompt(args)

    def _try_auto_route(self, text: str) -> str | None:
        """Match user intent against registered skills without an explicit ``/`` prefix.

        Scores every user-invocable skill and invokes the best match when its
        confidence exceeds the threshold (0.4).  Returns ``None`` when no skill
        is confident enough, letting the message fall through to the plain LLM.
        """
        from openvibe.skill.registry import get_registry

        if text.startswith("/"):
            return None  # already handled by _try_skill

        registry = get_registry()
        best_skill = None
        best_score = 0.0

        for skill in registry.all():
            if not skill.user_invocable:
                continue
            score = skill.match_intent(text)
            if score > best_score:
                best_score = score
                best_skill = skill

        if best_skill is None or best_score < 0.4:
            return None

        args = best_skill.extract_args(text)
        return best_skill.get_prompt(args)

    def _send_raw(
        self,
        text: str,
        on_token: Callable[[str], None] | None = None,
    ) -> Response:
        """Send *text* directly to the LLM without command/skill interception.

        Used internally by the :class:`~openvibe.skill.executor.SkillExecutor`
        so that retry prompts bypass the skill expansion layer.  Assumes the
        FSM is already in THINKING state when called from within the skill
        executor loop.
        """
        self._launch_worker(text, on_token, callback=None)
        return self._collect()

    def send(
        self,
        text: str,
        on_token: Callable[[str], None] | None = None,
        on_message: Callable[[str, str], None] | None = None,
        on_tool: Callable[[str, int, Any], None] | None = None,
    ) -> Response:
        """Send *text* to the agent and block until a result is ready.

        Returns when:
        * the turn finishes           → Response(state=IDLE)
        * a permission request fires  → Response(state=WAITING)
        * an error occurs             → Response(state=ERROR)

        Slash commands (``/help``, ``/cost``, etc.) are handled locally and
        never reach the LLM.  Skill invocations (``/simplify``, ``/debug``,
        etc.) are expanded into full LLM prompts before being sent.

        *on_message(msg_id, role)* — called when a new message is created.
        *on_tool(msg_id, part_index, state_dict)* — called on tool state changes.
        """
        # 1. Slash commands bypass the LLM entirely.
        cmd_response = self._try_command(text)
        if cmd_response is not None:
            return cmd_response

        # 2. Explicit skill invocations (``/name args``): expand prompt.
        expanded = self._try_skill(text)
        if expanded is not None:
            text = expanded
        else:
            # 3. Intent-based auto-routing: match natural language to skills.
            auto = self._try_auto_route(text)
            if auto is not None:
                text = auto

        with self._lock:
            if self._state not in (SessionState.IDLE, SessionState.ERROR):
                raise InvalidStateError(
                    f"send() requires IDLE state; current: {self._state}"
                )
            self._state = SessionState.THINKING
            self._abort_ev.clear()

        self._on_message = on_message
        self._on_tool = on_tool
        self._launch_worker(text, on_token, callback=None)
        return self._collect()

    def reply(
        self,
        request_id: str,
        option: str,
        on_token: Callable[[str], None] | None = None,  # noqa: ARG002 (future use)
    ) -> Response:
        """Reply to a pending InputRequest and block for the next result.

        *option* must be one of the ``Option.value`` strings from the request.
        """
        with self._lock:
            if self._state != SessionState.WAITING:
                raise InvalidStateError(
                    f"reply() requires WAITING state; current: {self._state}"
                )
            self._state = SessionState.THINKING

        self._resume_q.put((request_id, option))
        return self._collect()

    # ------------------------------------------------------------------
    # Non-blocking API
    # ------------------------------------------------------------------

    def send_nowait(
        self,
        text: str,
        callback: Callable[[Response], None] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_message: Callable[[str, str], None] | None = None,
        on_tool: Callable[[str, int, Any], None] | None = None,
    ) -> None:
        """Send *text* and return immediately (state → THINKING).

        *callback* is invoked (in a daemon thread) whenever the state
        changes to WAITING, IDLE, or ERROR.  If omitted, poll .state.

        Slash commands are executed synchronously and delivered via *callback*
        immediately.
        """
        cmd_response = self._try_command(text)
        if cmd_response is not None:
            if callback:
                callback(cmd_response)
            return

        # Explicit skill invocations (``/name args``): expand prompt.
        expanded = self._try_skill(text)
        if expanded is not None:
            text = expanded
        else:
            # Intent-based auto-routing.
            auto = self._try_auto_route(text)
            if auto is not None:
                text = auto

        with self._lock:
            if self._state not in (SessionState.IDLE, SessionState.ERROR):
                raise InvalidStateError(
                    f"send_nowait() requires IDLE state; current: {self._state}"
                )
            self._state = SessionState.THINKING
            self._abort_ev.clear()

        self._on_message = on_message
        self._on_tool = on_tool
        self._launch_worker(text, on_token, callback=callback)

    def reply_nowait(
        self,
        request_id: str,
        option: str,
        callback: Callable[[Response], None] | None = None,
        on_token: Callable[[str], None] | None = None,  # noqa: ARG002
    ) -> None:
        """Reply to a pending request and return immediately.

        *callback* is invoked (in a daemon thread) with the next Response.
        """
        with self._lock:
            if self._state != SessionState.WAITING:
                raise InvalidStateError(
                    f"reply_nowait() requires WAITING state; current: {self._state}"
                )
            self._state = SessionState.THINKING

        self._resume_q.put((request_id, option))

        if callback:
            threading.Thread(
                target=self._collect_and_call,
                args=(callback,),
                daemon=True,
            ).start()

    def resume_interrupted(
        self,
        allow: bool,
        on_token: Callable[[str], None] | None = None,
        on_message: Callable[[str, str], None] | None = None,
        on_tool: Callable[[str, int, Any], None] | None = None,
    ) -> Response:
        """Resume a session interrupted mid-tool without adding a new user message.

        Executes (allow=True) or denies (allow=False) any ToolParts with
        output=None, then calls the LLM to produce the final response.
        Requires the async processor path (start_async).
        """
        with self._lock:
            if self._state != SessionState.IDLE:
                raise InvalidStateError(
                    f"resume_interrupted() requires IDLE state; current: {self._state}"
                )
            if self._processor is None:
                raise RuntimeError(
                    "resume_interrupted() requires the async processor (use start_async())"
                )
            self._state = SessionState.THINKING
            self._abort_ev.clear()

        from openvibe.agent.agent import resolve as _resolve

        agent = _resolve(self._config, self._agent_name)

        self._on_message = on_message
        self._on_tool = on_tool
        self._worker = threading.Thread(
            target=_run_interrupted_async_threaded,
            args=(
                allow,
                on_token,
                on_message,
                on_tool,
                None,
                self._info,
                agent,
                self._db,
                self._processor,
                self._bus,
                self._permissions,
                self._result_q,
                self._resume_q,
                self._abort_ev,
            ),
            daemon=True,
            name=f"openvibe-resume-{self._info.id[:8]}",
        )
        self._worker.start()
        return self._collect()

    # ------------------------------------------------------------------
    # Abort
    # ------------------------------------------------------------------

    def abort(self, timeout: float = 5.0) -> None:
        """Signal the worker to stop and wait up to *timeout* seconds."""
        self._abort_ev.set()
        # If the worker is blocked waiting for a permission reply, unblock it
        # with a sentinel so the run_in_executor thread can exit.
        try:
            self._resume_q.put_nowait(("__abort__", "deny"))
        except queue.Full:
            pass
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=timeout)
        with self._lock:
            self._state = SessionState.IDLE

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _launch_worker(
        self,
        text: str,
        on_token: Callable[[str], None] | None,
        callback: Callable[[Response], None] | None,
    ) -> None:
        from openvibe.agent.agent import resolve as _resolve

        agent = _resolve(self._config, self._agent_name)

        if self._processor is not None:
            # Async processor path — full-featured (bus events, real-time tools, etc.)
            self._worker = threading.Thread(
                target=_run_turn_async_threaded,
                args=(
                    text,
                    on_token,
                    self._on_message,
                    self._on_tool,
                    callback,
                    self._info,
                    agent,
                    self._db,
                    self._processor,
                    self._bus,
                    self._permissions,
                    self._result_q,
                    self._resume_q,
                    self._abort_ev,
                ),
                daemon=True,
                name=f"openvibe-worker-{self._info.id[:8]}",
            )
        else:
            # Sync litellm path — used by tests and headless scripts
            self._worker = threading.Thread(
                target=_run_turn,
                args=(
                    text,
                    on_token,
                    callback,
                    self._info,
                    agent,
                    self._db,
                    self._registry,
                    self._result_q,
                    self._resume_q,
                    self._abort_ev,
                    self._llm,
                ),
                daemon=True,
                name=f"openvibe-worker-{self._info.id[:8]}",
            )
        self._worker.start()

    def _collect(self) -> Response:
        """Block until the worker pushes a Response; update local state."""
        response = self._result_q.get()
        with self._lock:
            self._state = response.state
        return response

    def _collect_and_call(self, callback: Callable[[Response], None]) -> None:
        """Collect one Response and invoke *callback* (runs in daemon thread)."""
        response = self._result_q.get()
        with self._lock:
            self._state = response.state
        callback(response)


# ---------------------------------------------------------------------------
# OpenVibe
# ---------------------------------------------------------------------------


class OpenVibe:
    """Top-level handle; create once and reuse across sessions.

    Preferred usage — context manager (handles start/close automatically)::

        with OpenVibe(project_dir=Path(".")) as ov:
            session = ov.create_session()
            ...

    Manual lifecycle::

        ov = OpenVibe()
        ov.start()
        try:
            ...
        finally:
            ov.close()
    """

    def __init__(
        self,
        project_dir: Path | None = None,
        config: Any | None = None,  # openvibe.config.Config
        db: Any | None = None,  # Database — inject for testing
        llm: Any | None = None,  # sync LLM callable — inject for testing
    ) -> None:
        self._project_dir = (project_dir or Path.cwd()).resolve()
        self._config = config
        self._db: Any = db  # None means create on start()
        self._llm: Any = llm  # None means use litellm
        self._registry: Any = None
        self._project: Any = None
        self._mcp: Any = None  # McpClientManager — kept alive to prevent GC
        # Async-path components (populated by start_async())
        self._bus: Any = None
        self._permissions: Any = None
        self._processor: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> "OpenVibe":
        """Initialise DB, tool registry, and MCP connections."""
        from openvibe.config import load_config
        from openvibe.db import create_database
        from openvibe.project import project as _project_module
        from openvibe.skill.bundled import init_bundled_skills
        from openvibe.skill.loader import load_skills_dir
        from openvibe.tool.base import create_default_registry

        if self._config is None:
            self._config = load_config(self._project_dir)

        if self._db is None:
            self._db = create_database()
        self._registry = create_default_registry()
        self._project = _project_module.get_or_create(self._db, self._project_dir)

        init_bundled_skills()
        load_skills_dir(self._project_dir / "skills")

        if self._config.mcp:
            self._init_mcp()

        return self

    def close(self) -> None:
        """Release DB connection (MCP connections are daemon threads)."""
        if self._db is not None:
            self._db.close()
            self._db = None

    def __enter__(self) -> "OpenVibe":
        return self.start()

    def __exit__(self, *_: Any) -> None:
        self.close()

    async def start_async(self) -> "OpenVibe":
        """Async startup — use this when running inside an async context (e.g. Textual TUI).

        Initialises all components including the full async processor stack
        (EventBus, SessionProcessor, PermissionService) so that sessions
        created from this instance get real-time bus events.
        """
        from openvibe.bus import EventBus
        from openvibe.config import load_config
        from openvibe.db import create_database
        from openvibe.llm import create_default_backend
        from openvibe.mcp.client import McpClientManager
        from openvibe.permission.permission import PermissionService
        from openvibe.project import project as _project_module
        from openvibe.session.processor import SessionProcessor
        from openvibe.skill.bundled import init_bundled_skills
        from openvibe.skill.loader import load_skills_dir
        from openvibe.tool.base import create_default_registry

        if self._config is None:
            self._config = load_config(self._project_dir)
        if self._db is None:
            self._db = create_database()

        init_bundled_skills()
        load_skills_dir(self._project_dir / "skills")

        llm = self._llm or create_default_backend()
        self._bus = EventBus()
        self._registry = create_default_registry()
        self._permissions = PermissionService(self._db, self._bus)

        mcp = McpClientManager()
        if self._config.mcp:
            mcp_tools = await mcp.connect_all(self._config.mcp)
            for tool in mcp_tools:
                self._registry.register(tool)
        self._mcp = mcp

        self._project = _project_module.get_or_create(self._db, self._project_dir)
        self._processor = SessionProcessor(
            self._db, llm, self._bus, self._registry, self._permissions
        )
        self._llm = llm
        return self

    async def close_async(self) -> None:
        """Async cleanup — closes MCP connections then the database."""
        if self._mcp is not None:
            await self._mcp.close_all()
            self._mcp = None
        if self._db is not None:
            self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def project_dir(self) -> Path:
        return self._project_dir

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def list_sessions(self) -> list[Any]:  # list[SessionInfo]
        self._require_started()
        from openvibe.session import session as _store

        return _store.list_sessions(self._db, self._project.id)

    def create_session(
        self,
        agent: str = "build",
        title: str | None = None,
    ) -> Session:
        self._require_started()
        from openvibe.session import session as _store

        info = _store.create(
            self._db,
            project_id=self._project.id,
            directory=str(self._project_dir),
            title=title,
        )
        return Session(
            info,
            self._db,
            self._registry,
            self._config,
            agent,
            self._llm,
            processor=self._processor,
            bus=self._bus,
            permissions=self._permissions,
        )

    def get_session(self, session_id: str, agent: str = "build") -> Session:
        self._require_started()
        from openvibe.session import session as _store

        info = _store.get(self._db, session_id)
        if info is None:
            raise KeyError(f"Session not found: {session_id!r}")
        return Session(
            info,
            self._db,
            self._registry,
            self._config,
            agent,
            self._llm,
            processor=self._processor,
            bus=self._bus,
            permissions=self._permissions,
        )

    def delete_session(self, session_id: str) -> None:
        self._require_started()
        from openvibe.session import session as _store

        _store.archive(self._db, session_id)

    # ------------------------------------------------------------------
    # One-shot convenience
    # ------------------------------------------------------------------

    def run(
        self,
        text: str,
        agent: str = "build",
        on_token: Callable[[str], None] | None = None,
        on_permission: str = "allow",  # "allow" | "deny" | "ask"
    ) -> Response:
        """Create a session, run *text* to completion, and return.

        *on_permission* controls what happens when the agent requests
        permission to run a tool:

        * ``"allow"`` — auto-approve (default for headless use)
        * ``"deny"``  — auto-deny
        * ``"ask"``   — raise RuntimeError; use send()/reply() instead
        """
        session = self.create_session(agent=agent)
        response = session.send(text, on_token=on_token)

        while response.state == SessionState.WAITING:
            if on_permission == "allow":
                response = session.reply(response.request.id, "allow")
            elif on_permission == "deny":
                response = session.reply(response.request.id, "deny")
            else:
                raise RuntimeError(
                    "Agent requested permission but on_permission='ask'. "
                    "Use send()/reply() to handle permissions interactively."
                )

        return response

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _require_started(self) -> None:
        if self._db is None:
            raise RuntimeError(
                "OpenVibe not started — use `with OpenVibe() as ov:` "
                "or call ov.start() first."
            )

    def _init_mcp(self) -> None:
        """Run MCP async initialisation once (blocks, then stores tools)."""
        import asyncio

        from openvibe.mcp.client import McpClientManager

        mcp = McpClientManager()
        try:
            tools = asyncio.run(mcp.connect_all(self._config.mcp))
            for tool in tools:
                self._registry.register(tool)
            self._mcp = mcp
        except Exception as exc:
            logger.warning("MCP init failed: %s", exc)


# ---------------------------------------------------------------------------
# Async worker — runs in a background thread via asyncio.run()
# ---------------------------------------------------------------------------


def _run_turn_async_threaded(
    text: str,
    on_token: Callable[[str], None] | None,
    on_message: Callable[[str, str], None] | None,
    on_tool: Callable[[str, int, Any], None] | None,
    callback: Callable[["Response"], None] | None,
    session_info: Any,
    agent: Any,
    db: Any,
    processor: Any,
    bus: Any,
    permissions: Any,
    result_q: "queue.Queue[Response]",
    resume_q: "queue.Queue[tuple[str, str]]",
    abort_ev: threading.Event,
) -> None:
    """Spawn a fresh event loop in this thread and run the async processor."""
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _run_turn_async(
                text,
                on_token,
                on_message,
                on_tool,
                callback,
                session_info,
                agent,
                db,
                processor,
                bus,
                permissions,
                result_q,
                resume_q,
                abort_ev,
            )
        )
    finally:
        loop.close()
        asyncio.set_event_loop(None)


async def _run_turn_async(
    text: str,
    on_token: Callable[[str], None] | None,
    on_message: Callable[[str, str], None] | None,
    on_tool: Callable[[str, int, Any], None] | None,
    callback: Callable[["Response"], None] | None,
    session_info: Any,
    agent: Any,
    db: Any,
    processor: Any,
    bus: Any,
    permissions: Any,
    result_q: "queue.Queue[Response]",
    resume_q: "queue.Queue[tuple[str, str]]",
    abort_ev: threading.Event,
) -> None:
    """Run one turn via the full async processor, translating bus events to callbacks.

    Permission handling: when a PermissionRequestedEvent arrives, we pause by
    putting Response(WAITING) in result_q and waiting (non-blocking) on
    resume_q for the caller's reply, then forward it to PermissionService.
    """
    import asyncio as _asyncio

    from openvibe.config import MessageRole, PermissionAction
    from openvibe.permission.permission import PermissionRequestedEvent
    from openvibe.session import session as _store
    from openvibe.session.models import (MessageCreatedEvent,
                                         ReasoningDeltaEvent, TextDeltaEvent,
                                         TextPart, ToolStateChangedEvent,
                                         TurnCompletedEvent)

    accumulated_text = ""

    # Bridge the threading abort event to an asyncio.Event in this loop.
    abort_async = _asyncio.Event()

    async def _watch_abort() -> None:
        loop = _asyncio.get_running_loop()
        await loop.run_in_executor(None, abort_ev.wait)
        abort_async.set()

    abort_watcher = _asyncio.create_task(_watch_abort())

    try:
        # Create the user message before subscribing so no duplicate is made.
        user_msg = _store.add_message(
            db, session_info.id, MessageRole.USER, [TextPart(content=text)]
        )
        if on_message:
            on_message(user_msg.id, "user")

        subscribed = _asyncio.Event()

        async def run_processor() -> None:
            exc_to_raise = None
            success = False
            try:
                await subscribed.wait()
                await processor.run(
                    session_info, agent, text, abort_async, user_message=user_msg
                )
                success = True
            except Exception as exc:  # noqa: BLE001
                exc_to_raise = exc
            finally:
                if not success:
                    # Ensure consume_events() can exit even on error.
                    await bus.publish(
                        TurnCompletedEvent(session_id=session_info.id, message_id="")
                    )
            if exc_to_raise:
                raise exc_to_raise

        async def consume_events() -> None:
            nonlocal accumulated_text
            async with bus.subscribe() as bus_events:
                subscribed.set()
                async for event in bus_events:
                    if getattr(event, "session_id", None) != session_info.id:
                        continue

                    if isinstance(event, MessageCreatedEvent) and event.message:
                        msg = event.message
                        if on_message and str(msg.role) == "assistant":
                            on_message(msg.id, "assistant")

                    elif isinstance(event, TextDeltaEvent):
                        accumulated_text += event.content
                        if on_token:
                            on_token(event.content)

                    elif isinstance(event, ReasoningDeltaEvent):
                        pass  # reasoning tokens not surfaced in the public API

                    elif isinstance(event, ToolStateChangedEvent):
                        if on_tool:
                            on_tool(
                                event.message_id, event.part_index, event.state or {}
                            )

                    elif isinstance(event, PermissionRequestedEvent):
                        req = InputRequest(
                            id=event.request_id,
                            kind="permission",
                            description=event.description or f"Allow '{event.tool}'?",
                            tool=event.tool,
                            argument=event.argument,
                            options=[
                                Option("allow", "Allow once"),
                                Option("allow_always", "Always allow"),
                                Option("deny", "Deny"),
                            ],
                        )
                        messages = _store.list_messages(db, session_info.id)
                        result_q.put(
                            Response(
                                state=SessionState.WAITING,
                                text=accumulated_text,
                                messages=messages,
                                request=req,
                            )
                        )

                        # Wait for the caller's reply without blocking this event loop.
                        loop = _asyncio.get_running_loop()
                        _req_id, option = await loop.run_in_executor(None, resume_q.get)

                        # Resolve the permission Future inside this loop.
                        decision = (
                            PermissionAction.ALLOW
                            if option in ("allow", "allow_always")
                            else PermissionAction.DENY
                        )
                        remember = option == "allow_always"
                        permissions.reply(
                            request_id=_req_id,
                            decision=decision,
                            remember=remember,
                            project_id=session_info.project_id,
                            tool=event.tool,
                            argument=event.argument,
                        )

                    elif isinstance(event, TurnCompletedEvent):
                        break

        await _asyncio.gather(run_processor(), consume_events())

        final_messages = _store.list_messages(db, session_info.id)
        response = Response(
            state=SessionState.IDLE,
            text=accumulated_text,
            messages=final_messages,
        )

    except Exception as exc:  # noqa: BLE001
        kind, msg_str = _classify_error(exc)
        response = Response(
            state=SessionState.ERROR,
            text=accumulated_text,
            error=ErrorInfo(kind=kind, message=msg_str),
        )

    finally:
        abort_watcher.cancel()
        try:
            await abort_watcher
        except _asyncio.CancelledError:
            pass

    result_q.put(response)
    if callback:
        callback(response)


# ---------------------------------------------------------------------------
# Interrupted-resume async worker
# ---------------------------------------------------------------------------


def _run_interrupted_async_threaded(
    allow: bool,
    on_token: Callable[[str], None] | None,
    on_message: Callable[[str, str], None] | None,
    on_tool: Callable[[str, int, Any], None] | None,
    callback: Callable[["Response"], None] | None,
    session_info: Any,
    agent: Any,
    db: Any,
    processor: Any,
    bus: Any,
    permissions: Any,
    result_q: "queue.Queue[Response]",
    resume_q: "queue.Queue[tuple[str, str]]",
    abort_ev: threading.Event,
) -> None:
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _run_interrupted_async(
                allow,
                on_token,
                on_message,
                on_tool,
                callback,
                session_info,
                agent,
                db,
                processor,
                bus,
                permissions,
                result_q,
                resume_q,
                abort_ev,
            )
        )
    finally:
        loop.close()
        asyncio.set_event_loop(None)


async def _run_interrupted_async(
    allow: bool,
    on_token: Callable[[str], None] | None,
    on_message: Callable[[str, str], None] | None,
    on_tool: Callable[[str, int, Any], None] | None,
    callback: Callable[["Response"], None] | None,
    session_info: Any,
    agent: Any,
    db: Any,
    processor: Any,
    bus: Any,
    permissions: Any,
    result_q: "queue.Queue[Response]",
    resume_q: "queue.Queue[tuple[str, str]]",
    abort_ev: threading.Event,
) -> None:
    """Resume a session interrupted mid-tool: execute the tool (or deny it),
    then continue the LLM turn — without creating a new user message."""
    import asyncio as _asyncio

    from openvibe.config import MessageRole, PermissionAction
    from openvibe.permission.permission import PermissionRequestedEvent
    from openvibe.session import session as _store
    from openvibe.session.models import (MessageCreatedEvent,
                                         ReasoningDeltaEvent, TextDeltaEvent,
                                         ToolStateChangedEvent,
                                         TurnCompletedEvent)

    accumulated_text = ""
    abort_async = _asyncio.Event()

    async def _watch_abort() -> None:
        loop = _asyncio.get_running_loop()
        await loop.run_in_executor(None, abort_ev.wait)
        abort_async.set()

    abort_watcher = _asyncio.create_task(_watch_abort())

    try:
        subscribed = _asyncio.Event()

        async def run_processor() -> None:
            exc_to_raise = None
            success = False
            try:
                await subscribed.wait()
                await processor.resume_interrupted(
                    session_info, agent, allow, abort_async
                )
                success = True
            except Exception as exc:  # noqa: BLE001
                exc_to_raise = exc
            finally:
                if not success:
                    await bus.publish(
                        TurnCompletedEvent(session_id=session_info.id, message_id="")
                    )
            if exc_to_raise:
                raise exc_to_raise

        async def consume_events() -> None:
            nonlocal accumulated_text
            async with bus.subscribe() as bus_events:
                subscribed.set()
                async for event in bus_events:
                    if getattr(event, "session_id", None) != session_info.id:
                        continue

                    if isinstance(event, MessageCreatedEvent) and event.message:
                        msg = event.message
                        if on_message and str(msg.role) == "assistant":
                            on_message(msg.id, "assistant")

                    elif isinstance(event, TextDeltaEvent):
                        accumulated_text += event.content
                        if on_token:
                            on_token(event.content)

                    elif isinstance(event, ReasoningDeltaEvent):
                        pass

                    elif isinstance(event, ToolStateChangedEvent):
                        if on_tool:
                            on_tool(
                                event.message_id, event.part_index, event.state or {}
                            )

                    elif isinstance(event, PermissionRequestedEvent):
                        req = InputRequest(
                            id=event.request_id,
                            kind="permission",
                            description=event.description or f"Allow '{event.tool}'?",
                            tool=event.tool,
                            argument=event.argument,
                            options=[
                                Option("allow", "Allow once"),
                                Option("allow_always", "Always allow"),
                                Option("deny", "Deny"),
                            ],
                        )
                        messages = _store.list_messages(db, session_info.id)
                        result_q.put(
                            Response(
                                state=SessionState.WAITING,
                                text=accumulated_text,
                                messages=messages,
                                request=req,
                            )
                        )

                        loop = _asyncio.get_running_loop()
                        _req_id, option = await loop.run_in_executor(None, resume_q.get)

                        decision = (
                            PermissionAction.ALLOW
                            if option in ("allow", "allow_always")
                            else PermissionAction.DENY
                        )
                        remember = option == "allow_always"
                        permissions.reply(
                            request_id=_req_id,
                            decision=decision,
                            remember=remember,
                            project_id=session_info.project_id,
                            tool=event.tool,
                            argument=event.argument,
                        )

                    elif isinstance(event, TurnCompletedEvent):
                        break

        await _asyncio.gather(run_processor(), consume_events())

        final_messages = _store.list_messages(db, session_info.id)
        response = Response(
            state=SessionState.IDLE,
            text=accumulated_text,
            messages=final_messages,
        )

    except Exception as exc:  # noqa: BLE001
        kind, msg_str = _classify_error(exc)
        response = Response(
            state=SessionState.ERROR,
            text=accumulated_text,
            error=ErrorInfo(kind=kind, message=msg_str),
        )

    finally:
        abort_watcher.cancel()
        try:
            await abort_watcher
        except _asyncio.CancelledError:
            pass

    result_q.put(response)
    if callback:
        callback(response)


# ---------------------------------------------------------------------------
# Sync worker — runs entirely in a background thread
# ---------------------------------------------------------------------------


def _run_turn(
    text: str,
    on_token: Callable[[str], None] | None,
    callback: Callable[[Response], None] | None,
    session_info: Any,
    agent: Any,  # AgentInfo
    db: Any,  # Database
    registry: Any,  # ToolRegistry
    result_q: "queue.Queue[Response]",
    resume_q: "queue.Queue[tuple[str, str]]",
    abort_ev: threading.Event,
    llm: Any = None,  # sync callable(model, messages, **kw) → iterable[chunk]
) -> None:
    """Full agent loop running synchronously in a worker thread.

    Communication protocol:
    * Pushes Response(WAITING)  to result_q when a permission request fires,
      then blocks on resume_q for the caller's (request_id, option) reply.
    * Pushes Response(IDLE|ERROR) to result_q when the turn finishes.
    * If *callback* is set, calls it with the final Response too.
    """
    from openvibe.config import MessageRole, ToolStateStatus
    from openvibe.session import session as _store
    from openvibe.session.models import TextPart, ToolPart, ToolState

    accumulated_text = ""

    try:
        # 1. Persist the user message
        _store.add_message(
            db, session_info.id, MessageRole.USER, [TextPart(content=text)]
        )

        # 2. Tool definitions for litellm (respecting agent's disabled list)
        disabled = set(agent.disabled_tools or [])
        ll_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters_schema(),
                },
            }
            for t in registry.all()
            if t.name not in disabled
        ]

        # 3. Resolved permission rules and system prompt
        rules = list(agent.permission_rules)
        system_prompt = _build_system_prompt(agent)

        doom_counts: dict[str, int] = {}

        # 4. Main agent loop — each iteration is one LLM call
        for _step in range(agent.max_steps or 50):
            if abort_ev.is_set():
                break

            # Reload full history so tool results from the last step are visible
            history = _store.list_messages(db, session_info.id)
            ll_messages = _messages_to_litellm(history)
            if system_prompt:
                ll_messages = [
                    {"role": "system", "content": system_prompt}
                ] + ll_messages

            # Create the assistant message shell for this step
            asst_msg = _store.add_message(db, session_info.id, MessageRole.ASSISTANT)
            part_index = 0  # next free part slot on asst_msg
            step_text = ""

            # Pending tool calls accumulated across stream chunks: index → state
            pending: dict[int, dict[str, Any]] = {}

            # -- LLM call (sync streaming) --
            call_kwargs: dict[str, Any] = {"stream": True}
            if ll_tools:
                call_kwargs["tools"] = ll_tools
            if agent.temperature is not None:
                call_kwargs["temperature"] = agent.temperature
            if agent.top_p is not None:
                call_kwargs["top_p"] = agent.top_p

            if llm is not None:
                _llm_call = llm
            else:
                import litellm  # lazy — keeps startup fast and tests free

                _llm_call = litellm.completion
            stream = _llm_call(
                model=_model_string(agent),
                messages=ll_messages,
                **call_kwargs,
            )

            for chunk in stream:
                if abort_ev.is_set():
                    break

                choice = chunk.choices[0]
                delta = choice.delta

                # Text token
                if delta.content:
                    step_text += delta.content
                    accumulated_text += delta.content
                    if on_token:
                        on_token(delta.content)

                # Tool call fragments
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in pending:
                            pending[idx] = {
                                "id": tc.id or "",
                                "name": "",
                                "args": "",
                            }
                        if tc.function:
                            if tc.function.name:
                                pending[idx]["name"] = tc.function.name
                            if tc.function.arguments:
                                pending[idx]["args"] += tc.function.arguments
                            if tc.id and tc.id != pending[idx]["id"]:
                                pending[idx]["id"] = tc.id

                if choice.finish_reason:
                    # Capture token usage
                    usage = getattr(chunk, "usage", None)
                    if usage:
                        _store.update_cost(
                            db,
                            session_info.id,
                            cost=0.0,
                            input_tokens=getattr(usage, "prompt_tokens", 0),
                            output_tokens=getattr(usage, "completion_tokens", 0),
                        )
                    break

            # Persist text part if any
            if step_text:
                text_part = TextPart(content=step_text)
                _store.upsert_part(db, asst_msg.id, part_index, text_part)
                part_index += 1

            # No tool calls → turn is complete
            if not pending or abort_ev.is_set():
                break

            # 5. Execute each tool call
            for tc_data in pending.values():
                if abort_ev.is_set():
                    break

                name = tc_data["name"]
                args_str = tc_data["args"]
                call_id = tc_data["id"]

                try:
                    args = json.loads(args_str) if args_str.strip() else {}
                except json.JSONDecodeError:
                    args = {"_raw": args_str}

                # Doom-loop guard
                doom_key = f"{name}:{args_str}"
                doom_counts[doom_key] = doom_counts.get(doom_key, 0) + 1
                if doom_counts[doom_key] >= _DOOM_THRESHOLD:
                    _persist_tool_call(
                        db,
                        asst_msg.id,
                        part_index,
                        call_id,
                        name,
                        args,
                        output=f"Doom loop: '{name}' called {doom_counts[doom_key]}× "
                        f"with identical arguments.",
                        error=True,
                        status=ToolStateStatus.ERROR,
                    )
                    part_index += 1
                    continue

                # Permission check
                decision = _check_permission(
                    name,
                    args,
                    rules,
                    session_info,
                    accumulated_text,
                    _store.list_messages(db, session_info.id),
                    result_q,
                    resume_q,
                    abort_ev,
                )
                if decision == "abort":
                    break
                if decision == "deny":
                    _persist_tool_call(
                        db,
                        asst_msg.id,
                        part_index,
                        call_id,
                        name,
                        args,
                        output="Permission denied.",
                        error=True,
                        status=ToolStateStatus.ERROR,
                    )
                    part_index += 1
                    continue

                # Execute the tool
                tool = registry.get(name)
                if tool is None:
                    _persist_tool_call(
                        db,
                        asst_msg.id,
                        part_index,
                        call_id,
                        name,
                        args,
                        output=f"Unknown tool: '{name}'.",
                        error=True,
                        status=ToolStateStatus.ERROR,
                    )
                    part_index += 1
                    continue

                result = _call_tool_sync(
                    tool, session_info, asst_msg.id, agent.name, abort_ev, args, db
                )
                _persist_tool_call(
                    db,
                    asst_msg.id,
                    part_index,
                    call_id,
                    name,
                    args,
                    output=result.output,
                    error=result.error,
                    status=(
                        ToolStateStatus.ERROR
                        if result.error
                        else ToolStateStatus.COMPLETED
                    ),
                )
                part_index += 1

        # Turn complete
        final_messages = _store.list_messages(db, session_info.id)
        response = Response(
            state=SessionState.IDLE,
            text=accumulated_text,
            messages=final_messages,
        )

    except Exception as exc:
        kind, msg = _classify_error(exc)
        response = Response(
            state=SessionState.ERROR,
            text=accumulated_text,
            error=ErrorInfo(kind=kind, message=msg),
        )

    result_q.put(response)
    if callback:
        callback(response)


# ---------------------------------------------------------------------------
# Worker helpers
# ---------------------------------------------------------------------------


def _check_permission(
    name: str,
    args: dict[str, Any],
    rules: list[Any],  # list[openvibe.permission.permission.Rule]
    session_info: Any,
    accumulated_text: str,
    messages: list[Any],
    result_q: "queue.Queue[Response]",
    resume_q: "queue.Queue[tuple[str, str]]",
    abort_ev: threading.Event,
) -> str:  # "allow" | "deny" | "abort"
    """Evaluate permission rules; suspend the worker when action is 'ask'."""
    import fnmatch

    from openvibe.config import PermissionAction

    for rule in rules:
        if fnmatch.fnmatch(name, rule.tool):
            if rule.action == PermissionAction.ALLOW:
                return "allow"
            if rule.action == PermissionAction.DENY:
                return "deny"
            if rule.action == PermissionAction.ASK:
                break  # fall through to interactive ask

    # ASK flow: pause worker, let caller decide
    request_id = uuid.uuid4().hex
    request = InputRequest(
        id=request_id,
        kind="permission",
        description=f"Allow tool '{name}' to run?",
        options=[
            Option("allow", "Allow once"),
            Option("allow_always", "Always allow"),
            Option("deny", "Deny"),
        ],
    )
    result_q.put(
        Response(
            state=SessionState.WAITING,
            text=accumulated_text,
            messages=messages,
            request=request,
        )
    )

    try:
        _req_id, option = resume_q.get(timeout=_PERMISSION_TIMEOUT)
    except queue.Empty:
        logger.warning("Permission request timed out for tool '%s'", name)
        return "deny"

    if abort_ev.is_set():
        return "abort"

    return "allow" if option in ("allow", "allow_always") else "deny"


def _call_tool_sync(
    tool: Any,
    session_info: Any,
    msg_id: str,
    agent_name: str,
    abort_ev: threading.Event,
    args: dict[str, Any],
    db: Any,
) -> Any:  # ToolResult
    """Execute an (async) tool synchronously via a contained event loop."""
    import asyncio

    from openvibe.tool.base import ToolContext, ToolResult

    ctx = ToolContext(
        session_id=session_info.id,
        message_id=msg_id,
        agent_name=agent_name,
        project_id=session_info.project_id,
        working_dir=session_info.directory,
        abort=asyncio.Event(),
        call_id=uuid.uuid4().hex,
    )
    # Some tools (e.g. todo) need DB access
    ctx._db = db  # type: ignore[attr-defined]

    try:
        return asyncio.run(tool(ctx, args))
    except Exception as exc:
        from openvibe.tool.base import ToolResult

        return ToolResult(title=f"Error in {tool.name}", output=str(exc), error=True)


def _persist_tool_call(
    db: Any,
    msg_id: str,
    part_index: int,
    call_id: str,
    tool_name: str,
    input_args: dict[str, Any],
    output: str,
    error: bool,
    status: Any,
) -> None:
    """Write a ToolPart (call + result) to the parts table."""
    from openvibe.session import session as _store
    from openvibe.session.models import ToolPart, ToolState

    part = ToolPart(
        state=ToolState(
            status=status,
            call_id=call_id,
            tool_name=tool_name,
            input=input_args,
            output=output,
            error=output if error else None,
        )
    )
    _store.upsert_part(db, msg_id, part_index, part)


def _messages_to_litellm(messages: list[Any]) -> list[dict[str, Any]]:
    """Convert stored MessageInfo list to litellm-compatible message dicts.

    Reconstructs the required sequence for tool-use models:
      assistant message with tool_calls  →  one or more tool result messages
    """
    from openvibe.config import MessageRole
    from openvibe.session.models import TextPart, ToolPart

    result: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role not in (MessageRole.USER, MessageRole.ASSISTANT):
            continue

        if msg.role == MessageRole.USER:
            text = " ".join(
                p.content for p in msg.parts if isinstance(p, TextPart)
            ).strip()
            if text:
                result.append({"role": "user", "content": text})
            continue

        # ASSISTANT message
        text_parts = [p for p in msg.parts if isinstance(p, TextPart)]
        tool_parts = [
            p for p in msg.parts if isinstance(p, ToolPart) and p.state.call_id
        ]

        if not text_parts and not tool_parts:
            continue

        content = " ".join(p.content for p in text_parts).strip()
        d: dict[str, Any] = {"role": "assistant", "content": content or ""}

        if tool_parts:
            d["tool_calls"] = [
                {
                    "id": p.state.call_id,
                    "type": "function",
                    "function": {
                        "name": p.state.tool_name,
                        "arguments": json.dumps(p.state.input or {}),
                    },
                }
                for p in tool_parts
            ]
        result.append(d)

        # Tool results must immediately follow the assistant message.
        # If a tool was interrupted (app quit while waiting for permission),
        # output is None — emit a synthetic result so the LLM message sequence
        # remains valid (every tool_call must have a matching tool result).
        for p in tool_parts:
            content = (
                p.state.output
                if p.state.output is not None
                else "Tool execution was interrupted (session was closed before the tool completed)."
            )
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": p.state.call_id,
                    "content": content,
                }
            )

    return result


def _build_system_prompt(agent: Any) -> str:
    parts = [agent.system_prompt] + list(agent.extra_instructions or [])
    return "\n\n".join(p for p in parts if p)


def _model_string(agent: Any) -> str:
    if agent.model:
        return f"{agent.model.provider_id}/{agent.model.model_id}"
    return "anthropic/claude-sonnet-4-5"


def _classify_error(exc: Exception) -> tuple[str, str]:
    msg = str(exc).lower()
    if "auth" in msg or "api key" in msg or "unauthorized" in msg:
        return "auth", str(exc)
    if "context" in msg and ("length" in msg or "window" in msg or "limit" in msg):
        return "context_overflow", str(exc)
    return "api_error", str(exc)
