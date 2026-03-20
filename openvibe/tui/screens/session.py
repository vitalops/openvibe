"""Main chat screen — uses the public OpenVibe API exclusively."""

from __future__ import annotations

import queue as _queue
from typing import Any

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
        background: $background;
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
        # Queue used by the streaming worker to receive permission replies.
        self._perm_reply: _queue.Queue[tuple[str, str]] = _queue.Queue(maxsize=1)
        # Tracks the current assistant message ID for token routing.
        self._current_msg_id: str = ""
        # When a permission is resolved we store the permission message ID here
        # so that the completed tool widget is placed inside that message (below
        # the "→ allowed/denied" line) instead of the assistant message above it.
        self._perm_tool_target: str | None = None
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
        self.query_one(InputBar).focus_input()

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    async def _load_history(self, session: Any) -> None:
        msg_list = self.query_one(MessageList)
        for msg in session.messages():
            role = str(msg.role)
            widget = await msg_list.add_message(msg.id, role)
            for i, part in enumerate(msg.parts):
                if isinstance(part, TextPart):
                    widget.append_text(part.content)
                elif isinstance(part, ToolPart):
                    await widget.add_tool(i, part.state.model_dump())

    def _refresh_header(self, *, streaming: bool = False) -> None:
        title = self._session_title or self._session_id[:12]
        suffix = "  [dim]streaming…[/dim]" if streaming else ""
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

        input_bar = self.query_one(InputBar)
        input_bar.record_submission(event.text)
        input_bar.freeze()
        self._refresh_header(streaming=True)
        self._stream_message(event.text)

    @on(InputBar.HistoryNav)
    def handle_history_nav(self, event: InputBar.HistoryNav) -> None:
        self.query_one(InputBar).navigate_history(event.direction)

    # ------------------------------------------------------------------
    # Permission handling
    # ------------------------------------------------------------------

    async def _handle_permission_choice(self, text: str) -> None:
        pending = self._pending_permission
        self._pending_permission = None

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

        # Route the completed tool widget into this permission message.
        self._perm_tool_target = pending["message_id"]

        # Re-freeze while the worker waits for the tool to complete.
        self.query_one(InputBar).freeze()

        # Unblock the streaming worker.
        self._perm_reply.put((pending["request_id"], option))

    # ------------------------------------------------------------------
    # Streaming worker (runs in a background thread)
    # ------------------------------------------------------------------

    @work(thread=True)
    def _stream_message(self, text: str) -> None:
        """Run the agent turn in a background thread via Session.send().

        Callbacks route streaming events back to the TUI via call_from_thread.
        Permission requests pause the worker until the user replies via
        the _perm_reply queue.
        """
        session = self.app.get_session(self._session_id)  # type: ignore[attr-defined]

        def on_message(msg_id: str, role: str) -> None:
            self.app.call_from_thread(
                self.post_message, events.NewMessage(msg_id, role)
            )
            if role == "user":
                # Show the typed text immediately in the user message widget.
                self.app.call_from_thread(
                    self.post_message, events.TextDelta(msg_id, text)
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

        try:
            response = session.send(
                text,
                on_token=on_token,
                on_message=on_message,
                on_tool=on_tool,
            )

            while response.state == SessionState.WAITING:
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
                # Block this thread until the TUI user replies.
                req_id, option = self._perm_reply.get()
                response = session.reply(req_id, option)

            if response.state == SessionState.IDLE:
                self.app.call_from_thread(
                    self.post_message, events.TurnCompleted("")
                )
            else:
                error_msg = (
                    response.error.message if response.error else "Unknown error"
                )
                self.app.call_from_thread(
                    self.post_message, events.StreamError(error_msg)
                )

        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(
                self.post_message, events.StreamError(str(exc))
            )

    # ------------------------------------------------------------------
    # Widget event handlers
    # ------------------------------------------------------------------

    @on(events.NewMessage)
    async def handle_new_message(self, event: events.NewMessage) -> None:
        await self.query_one(MessageList).add_message(event.message_id, event.role)

    @on(events.TextDelta)
    def handle_text_delta(self, event: events.TextDelta) -> None:
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
    async def handle_permission_requested(self, event: events.PermissionRequested) -> None:
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
        input_bar.unfreeze()
        input_bar.set_status(
            "[dim]1[/dim] allow  [dim]2[/dim] always  [dim]3[/dim] deny   [dim](enter = 1)[/dim]"
        )

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

    def action_noop(self) -> None:
        pass
