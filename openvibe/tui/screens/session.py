"""Main chat screen — uses the public OpenVibe API exclusively."""

from __future__ import annotations

from typing import Any

from textual import events as textual_events
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static

from openvibe.api import SessionState
from openvibe.session.models import TextPart, ToolPart
from openvibe.tui import events
from openvibe.tui.widgets.input_bar import InputBar
from openvibe.tui.widgets.messages import MessageList


class SessionScreen(Screen):
    """Full-screen chat interface for one session."""

    DEFAULT_CSS = """
    SessionScreen {
        layout: vertical;
    }
    #header {
        height: 1;
        background: #000000;
        color: $text-muted;
        padding: 0 2;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "sessions", "Sessions", show=True),
        Binding("ctrl+n", "new_session", "New", show=True),
        Binding("escape", "noop", ""),
    ]

    def __init__(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id
        self._session_title: str | None = None
        self._pending_permission: dict | None = None
        # Tracks the current assistant message ID for token routing.
        self._current_msg_id: str = ""
        # When a permission is resolved we store the permission message ID here
        # so that the completed tool widget is placed inside that message (below
        # the "→ allowed/denied" line) instead of the assistant message above it.
        self._perm_tool_target: str | None = None
        # Approximate output token counter (incremented per TextDelta event).
        self._stream_token_count: int = 0
        super().__init__(**kwargs)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="header")
        yield MessageList(id="messages")
        yield InputBar(id="input-bar")

    async def on_mount(self) -> None:
        session = self.app.get_session(self._session_id)  # type: ignore[attr-defined]
        info = session.info
        self._session_title = info.title or None
        self._refresh_header()
        await self._load_history(session)

        input_bar = self.query_one(InputBar)

        if session.state == SessionState.WAITING:
            # Same-process case: navigated away and back without answering.
            # The live worker thread is still blocked waiting for our reply.
            pending = self.app.get_pending_permission(self._session_id)  # type: ignore[attr-defined]
            if pending:
                self._pending_permission = pending
                input_bar.enter_permission_mode()
        elif self._session_was_interrupted(session):
            # Restart case: app was closed while a permission prompt was pending.
            # The worker thread is gone; we cannot resume via reply().
            # Reconstruct the permission prompt so the user can allow/deny
            # normally.  On choice, a new turn is started; the processor will
            # inject a synthetic result for the interrupted tool so the LLM
            # can retry (allow) or cancel (deny).
            interrupted_tool = self._get_interrupted_tool(session)
            perm_msg = self._get_last_permission_message(session)
            if interrupted_tool and perm_msg:
                tool_name = interrupted_tool.state.tool_name
                arg_val = next(iter(interrupted_tool.state.input.values()), None)
                self._pending_permission = {
                    "request_id": None,
                    "tool": tool_name,
                    "description": f"run {tool_name}",
                    "argument": str(arg_val) if arg_val is not None else None,
                    "message_id": perm_msg.id,
                    "interrupted": True,
                }
                input_bar.enter_permission_mode()
            else:
                input_bar.set_status(
                    "[dim]Session was interrupted mid-tool. Send a message to continue.[/dim]"
                )

        input_bar.focus_input()

    @staticmethod
    def _session_was_interrupted(session: Any) -> bool:
        """Return True when the session has unfinished tool calls in its history."""
        return SessionScreen._get_interrupted_tool(session) is not None

    @staticmethod
    def _get_interrupted_tool(session: Any) -> "ToolPart | None":
        """Return the first ToolPart with no output (interrupted mid-permission)."""
        from openvibe.session.models import ToolPart

        for msg in session.messages():
            for part in msg.parts:
                if (
                    isinstance(part, ToolPart)
                    and part.state.call_id
                    and part.state.output is None
                ):
                    return part
        return None

    @staticmethod
    def _get_last_permission_message(session: Any) -> Any:
        """Return the last PERMISSION message from the session history, or None."""
        from openvibe.config import MessageRole

        last = None
        for msg in session.messages():
            if msg.role == MessageRole.PERMISSION:
                last = msg
        return last

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    async def _load_history(self, session: Any) -> None:
        from openvibe.config import MessageRole

        msg_list = self.query_one(MessageList)
        messages = session.messages()

        # Identify assistant messages immediately followed by a permission
        # message.  Their completed tool parts should render *after* the
        # permission prompt (on the permission widget), matching live behaviour.
        defer_from: set[str] = set()
        # Map: permission message id → list of (part_index, state_dict)
        deferred: dict[str, list[tuple[int, dict]]] = {}
        for idx, msg in enumerate(messages):
            if msg.role == MessageRole.PERMISSION and idx > 0:
                prev = messages[idx - 1]
                if prev.role == MessageRole.ASSISTANT:
                    defer_from.add(prev.id)
                    deferred[msg.id] = [
                        (i, part.state.model_dump())
                        for i, part in enumerate(prev.parts)
                        if isinstance(part, ToolPart) and part.state.output is not None
                    ]

        for msg in messages:
            role = str(msg.role)
            widget = await msg_list.add_message(msg.id, role)

            for i, part in enumerate(msg.parts):
                if isinstance(part, TextPart):
                    if role in ("permission", "system"):
                        # Rich markup — bypass the markdown pipeline.
                        widget.replace_text(part.content)
                    else:
                        widget.append_text(part.content)
                elif isinstance(part, ToolPart):
                    if part.state.output is None and part.state.call_id:
                        continue
                    # Skip tools deferred to a permission message.
                    if msg.id in defer_from:
                        continue
                    await widget.add_tool(i, part.state.model_dump())

            # After rendering the permission message text, mount the
            # deferred tool parts from the preceding assistant message.
            if msg.id in deferred:
                for part_idx, state_dict in deferred[msg.id]:
                    await widget.add_tool(part_idx, state_dict)

    @staticmethod
    def _fmt_tokens(n: int) -> str:
        if n >= 999_950:
            v = n / 1_000_000
            return f"{v:.2f}".rstrip("0").rstrip(".") + "M"
        if n >= 1_000:
            v = n / 1_000
            return f"{v:.1f}".rstrip("0").rstrip(".") + "k"
        return str(n)

    def _refresh_header(self, *, streaming: bool = False, token_count: int = 0) -> None:
        title = self._session_title or self._session_id[:12]
        if streaming:
            tok = f" ({self._fmt_tokens(token_count)} tokens)" if token_count else ""
            suffix = f"  [dim]streaming…{tok}[/dim]"
        else:
            suffix = ""
        self.query_one("#header", Static).update(
            f"[bold]openvibe[/bold]  [dim]{title}[/dim]{suffix}"
        )

    # ------------------------------------------------------------------
    # Input submission
    # ------------------------------------------------------------------

    @on(InputBar.Submitted)
    async def handle_submitted(self, event: InputBar.Submitted) -> None:
        if self._pending_permission:
            await self._handle_permission_choice(event.text)
            return

        # Slash commands — handled at the API level, but we intercept the
        # result here to avoid freezing the UI / showing a spinner.
        # Skills look like commands (/simplify, /plan, …) but are NOT in the
        # command registry — they must go through the LLM path (_start_turn).
        from openvibe.commands import get_command, has_command, is_command

        if is_command(event.text):
            parsed = get_command(event.text)
            if parsed and has_command(parsed[0]):
                await self._handle_command(event.text)
                return
            # Not a registered command — fall through to LLM path so that
            # Session._try_skill() can expand it.

        input_bar = self.query_one(InputBar)
        input_bar.record_submission(event.text)
        input_bar.freeze()
        self._stream_token_count = 0
        self._refresh_header(streaming=True)
        self._start_turn(event.text)

    @on(InputBar.HistoryNav)
    def handle_history_nav(self, event: InputBar.HistoryNav) -> None:
        self.query_one(InputBar).navigate_history(event.direction)

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    async def _handle_command(self, text: str) -> None:
        from openvibe.config import MessageRole
        from openvibe.session import session as session_store
        from openvibe.session.models import TextPart

        input_bar = self.query_one(InputBar)
        input_bar.record_submission(text)
        msg_list = self.query_one(MessageList)

        ov = self.app.ov  # type: ignore[attr-defined]
        db = ov._db  # type: ignore[attr-defined]

        # Persist and display the command as a user message.
        user_msg = session_store.add_message(
            db, self._session_id, MessageRole.USER, [TextPart(content=text)],
        )
        widget = await msg_list.add_message(user_msg.id, "user")
        widget.append_text(text)

        # Execute via the API layer.
        session = self.app.get_session(self._session_id)  # type: ignore[attr-defined]
        response = session.send(text)
        result = response.command_result

        if result is None:
            return

        # Handle special signals.
        if result.quit:
            self.app.exit()
            return

        if result.clear:
            for child in list(msg_list.children):
                child.remove()
            msg_list._messages.clear()
            return

        # Persist and display the result as a system message.
        # Command output is Rich markup, not markdown — use SYSTEM role so that
        # it bypasses the markdown renderer (render_markdown / mistune) and is
        # passed directly to Static.update() which interprets Rich markup tags.
        if result.output:
            reply_msg = session_store.add_message(
                db, self._session_id, MessageRole.SYSTEM,
                [TextPart(content=result.output)],
            )
            result_widget = await msg_list.add_message(
                reply_msg.id, str(MessageRole.SYSTEM),
            )
            result_widget.replace_text(result.output)

    # ------------------------------------------------------------------
    # Permission handling
    # ------------------------------------------------------------------

    async def _handle_permission_choice(self, text: str) -> None:
        pending = self._pending_permission
        self._pending_permission = None
        self.app.set_pending_permission(self._session_id, None)  # type: ignore[attr-defined]

        choice = text.strip().lower()
        match choice:
            case "1" | "allow" | "y" | "yes" | "":
                option = "allow"
                label = "[green]allowed[/green]"
            case "2" | "always":
                option = "allow_always"
                label = "[green]always allowed[/green]"
            case _:
                option = "deny"
                label = "[red]denied[/red]"

        msg_widget = self.query_one(MessageList).get_message(pending["message_id"])
        if msg_widget:
            tool = pending["tool"]
            description = pending["description"]
            argument = pending.get("argument")
            argument_line = ""
            if argument and argument != description:
                argument_line = f"\n[dim]  {argument}[/dim]"
            msg_widget.replace_text(
                f"⚠  [bold]{tool}[/bold]: {description}{argument_line}\n"
                f"[dim]→[/dim] {label}"
            )

        # Exit permission mode and re-freeze while the agent executes the tool.
        input_bar = self.query_one(InputBar)
        input_bar.exit_permission_mode()
        input_bar.freeze()

        if pending.get("interrupted"):
            # Restart case: no live worker thread exists.  Execute the tool
            # directly (allow) or inject a denied result, then continue the
            # LLM turn — without adding a new user message.
            if pending.get("message_id"):
                self._perm_tool_target = pending["message_id"]
            self._start_resume_interrupted_turn(option in ("allow", "allow_always"))
            return

        # Route the completed tool widget into this permission message.
        self._perm_tool_target = pending["message_id"]

        # Resume the agent turn in a fresh background worker.  This avoids
        # blocking the previous worker on a queue and means the screen can be
        # re-mounted at any point without leaving orphaned threads.
        self._resume_turn(pending["request_id"], option)

    # ------------------------------------------------------------------
    # Agent turn workers (each runs in a background thread and exits after
    # delivering one response — IDLE, WAITING, or ERROR)
    # ------------------------------------------------------------------

    def _make_callbacks(self, user_text: str | None) -> tuple:
        """Return (on_message, on_token, on_tool) callbacks for a turn.

        *user_text* is only needed for the initial send so the typed text can
        be echoed into the user message widget immediately.  Pass None when
        resuming after a permission reply.
        """

        def on_message(msg_id: str, role: str) -> None:
            self.app.call_from_thread(
                self.post_message, events.NewMessage(msg_id, role)
            )
            if role == "user" and user_text is not None:
                self.app.call_from_thread(
                    self.post_message, events.TextDelta(msg_id, user_text)
                )
            elif role == "assistant":
                self._current_msg_id = msg_id

        def on_token(token: str) -> None:
            self.app.call_from_thread(
                self.post_message, events.TextDelta(self._current_msg_id, token)
            )

        def on_tool(msg_id: str, part_index: int, state: dict) -> None:
            self.app.call_from_thread(
                self.post_message, events.ToolStateChanged(msg_id, part_index, state)
            )

        return on_message, on_token, on_tool

    def _dispatch_response(self, response: Any) -> None:
        """Route a completed Response to the appropriate TUI event."""
        if response.state == SessionState.WAITING:
            req = response.request
            self.app.call_from_thread(
                self.post_message,
                events.PermissionRequested(
                    req.id,
                    req.tool or "tool",
                    req.description,
                    self._session_id,
                    argument=req.argument,
                ),
            )
        elif response.state == SessionState.IDLE:
            self.app.call_from_thread(self.post_message, events.TurnCompleted(""))
        else:
            error_msg = response.error.message if response.error else "Unknown error"
            self.app.call_from_thread(self.post_message, events.StreamError(error_msg))

    @work(thread=True)
    def _start_turn(self, text: str) -> None:
        """Start a new agent turn with *text* as the user message."""
        session = self.app.get_session(self._session_id)  # type: ignore[attr-defined]
        on_message, on_token, on_tool = self._make_callbacks(text)
        try:
            response = session.send(
                text,
                on_token=on_token,
                on_message=on_message,
                on_tool=on_tool,
            )
            self._dispatch_response(response)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self.post_message, events.StreamError(str(exc)))

    @work(thread=True)
    def _resume_turn(self, request_id: str, option: str) -> None:
        """Resume a paused turn after the user has answered a permission request."""
        session = self.app.get_session(self._session_id)  # type: ignore[attr-defined]
        on_message, on_token, on_tool = self._make_callbacks(None)
        try:
            response = session.reply(request_id, option)
            self._dispatch_response(response)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self.post_message, events.StreamError(str(exc)))

    @work(thread=True)
    def _start_resume_interrupted_turn(self, allow: bool) -> None:
        """Resume a session interrupted mid-tool (restart case).

        Executes the tool directly (allow=True) or injects a denied result,
        then continues the LLM turn without adding a new user message.
        """
        session = self.app.get_session(self._session_id)  # type: ignore[attr-defined]
        on_message, on_token, on_tool = self._make_callbacks(None)
        try:
            response = session.resume_interrupted(
                allow,
                on_token=on_token,
                on_message=on_message,
                on_tool=on_tool,
            )
            self._dispatch_response(response)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self.post_message, events.StreamError(str(exc)))

    # ------------------------------------------------------------------
    # Widget event handlers
    # ------------------------------------------------------------------

    @on(events.NewMessage)
    async def handle_new_message(self, event: events.NewMessage) -> None:
        await self.query_one(MessageList).add_message(event.message_id, event.role)

    @on(events.TextDelta)
    def handle_text_delta(self, event: events.TextDelta) -> None:
        self._stream_token_count += 1
        self._refresh_header(streaming=True, token_count=self._stream_token_count)
        self.query_one(MessageList).append_text(event.message_id, event.content)

    @on(events.ToolStateChanged)
    async def handle_tool_state(self, event: events.ToolStateChanged) -> None:
        msg_list = self.query_one(MessageList)
        widget = msg_list.get_message(event.message_id)
        if not widget:
            return

        if event.part_index in widget._tools:
            widget.update_tool(event.part_index, event.state)
        else:
            # Don't create a widget for the initial "pending" state — the tool
            # hasn't started (permission may not be granted).  Wait for the
            # first meaningful state (running, completed, error).
            if event.state.get("status") == "pending":
                return
            # If a permission was just resolved, place the tool widget inside
            # the permission message (below the "→ allowed" line) so it
            # appears after the prompt rather than above it.
            target_widget = widget
            if self._perm_tool_target:
                perm_widget = msg_list.get_message(self._perm_tool_target)
                if perm_widget:
                    target_widget = perm_widget
                self._perm_tool_target = None
            await target_widget.add_tool(event.part_index, event.state)
            msg_list.scroll_end(animate=False)

    @on(events.TurnCompleted)
    def handle_turn_completed(self, _event: events.TurnCompleted) -> None:
        self.query_one(InputBar).enable()
        self._refresh_header()

    @on(events.PermissionRequested)
    async def handle_permission_requested(
        self, event: events.PermissionRequested
    ) -> None:
        from openvibe.config import MessageRole
        from openvibe.session import session as session_store
        from openvibe.session.models import TextPart

        ov = self.app.ov  # type: ignore[attr-defined]
        # Accessing the DB via the internal handle is acceptable here for
        # persisting the permission prompt message.
        db = ov._db  # type: ignore[attr-defined]

        description = event.description or f"run {event.tool}"
        # Show the actual argument (command/path) when it differs from the description.
        argument_line = ""
        if event.argument and event.argument != description:
            argument_line = f"\n[dim]  {event.argument}[/dim]"
        msg_text = (
            f"⚠  [bold]{event.tool}[/bold]: {description}{argument_line}\n"
            f"[dim]1[/dim] allow  [dim]2[/dim] allow always  [dim]3[/dim] deny"
            f"   [dim](enter = 1)[/dim]"
        )

        perm_msg = session_store.add_message(
            db,
            self._session_id,
            MessageRole.PERMISSION,
            [TextPart(content=msg_text)],
        )
        widget = await self.query_one(MessageList).add_message(
            perm_msg.id, str(MessageRole.PERMISSION)
        )
        widget.replace_text(msg_text)

        input_bar = self.query_one(InputBar)
        input_bar.enter_permission_mode()

        self._pending_permission = {
            "request_id": event.request_id,
            "tool": event.tool,
            "description": description,
            "argument": event.argument,
            "message_id": perm_msg.id,
        }

    @on(events.StreamError)
    async def handle_stream_error(self, event: events.StreamError) -> None:
        from openvibe.config import MessageRole
        from openvibe.session import session as session_store
        from openvibe.session.models import TextPart

        ov = self.app.ov  # type: ignore[attr-defined]
        db = ov._db  # type: ignore[attr-defined]

        error_msg = session_store.add_message(
            db,
            self._session_id,
            MessageRole.ERROR,
            [TextPart(content=event.message)],
        )
        widget = await self.query_one(MessageList).add_message(
            error_msg.id, str(MessageRole.ERROR)
        )
        widget.append_text(event.message)
        self.query_one(InputBar).enable()
        self._refresh_header()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_sessions(self) -> None:
        from openvibe.tui.screens.sessions import SessionListScreen

        def on_dismiss(session_id: str | None) -> None:
            if session_id and session_id != self._session_id:
                self.app.push_screen(SessionScreen(session_id))

        self.app.push_screen(SessionListScreen(), on_dismiss)

    def action_new_session(self) -> None:
        ov = self.app.ov  # type: ignore[attr-defined]
        session = ov.create_session()
        self.app.push_screen(SessionScreen(session.id))

    def on_key(self, event: textual_events.Key) -> None:
        input_bar = self.query_one(InputBar)
        if not input_bar.in_permission_mode:
            return
        if event.key in ("1", "enter"):
            event.stop()
            event.prevent_default()
            input_bar.post_message(InputBar.Submitted("1"))
        elif event.key == "2":
            event.stop()
            event.prevent_default()
            input_bar.post_message(InputBar.Submitted("2"))
        elif event.key == "3":
            event.stop()
            event.prevent_default()
            input_bar.post_message(InputBar.Submitted("3"))

    def action_noop(self) -> None:
        pass
