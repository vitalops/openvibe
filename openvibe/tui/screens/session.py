"""Main chat screen — communicates with the core directly, no HTTP."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static

from openvibe.agent import agent as agent_module
from openvibe.config import MessageRole, PermissionAction
from openvibe.permission.permission import PermissionRequestedEvent as BusPermissionRequested
from openvibe.project import project as project_module
from openvibe.session import session as session_store
from openvibe.session.models import (
    MessageCreatedEvent,
    MessageInfo,
    ReasoningDeltaEvent,
    TextDeltaEvent,
    TextPart,
    ToolPart,
    ToolStateChangedEvent,
    TurnCompletedEvent,
)
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
        super().__init__(**kwargs)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="header")
        yield MessageList(id="messages")
        yield InputBar(id="input-bar")

    async def on_mount(self) -> None:
        self._load_session()
        await self._load_history()
        self.query_one(InputBar).focus_input()

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    def _load_session(self) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        info = session_store.get(state.db, self._session_id)
        if info:
            self._session_title = info.title or None
        self._refresh_header()

    async def _load_history(self) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        messages = session_store.list_messages(state.db, self._session_id)
        msg_list = self.query_one(MessageList)
        for msg in messages:
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
        # If there's a pending permission request, treat input as the choice
        if self._pending_permission:
            await self._handle_permission_choice(event.text)
            return

        state = self.app.state  # type: ignore[attr-defined]
        input_bar = self.query_one(InputBar)
        input_bar.record_submission(event.text)
        input_bar.freeze()
        # Persist and display user message immediately — no waiting for the worker
        user_msg = session_store.add_message(
            state.db,
            self._session_id,
            MessageRole.USER,
            [TextPart(content=event.text)],
        )
        widget = await self.query_one(MessageList).add_message(user_msg.id, str(MessageRole.USER))
        widget.append_text(event.text)
        self._stream_message(event.text, user_msg)

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
            case "1" | "allow" | "y" | "yes":
                decision = PermissionAction.ALLOW
                remember = False
                label = "[green]allowed[/green]"
            case "2" | "always":
                decision = PermissionAction.ALLOW
                remember = True
                label = "[green]always allowed[/green]"
            case _:
                decision = PermissionAction.DENY
                remember = False
                label = "[red]denied[/red]"

        # Update the permission chat message to show the decision
        msg_widget = self.query_one(MessageList).get_message(pending["message_id"])
        if msg_widget:
            tool = pending["tool"]
            description = pending["description"]
            msg_widget.replace_text(
                f"⚠  [bold]{tool}[/bold]: {description}\n"
                f"[dim]→[/dim] {label}"
            )

        # Reply to the permission service (unblocks the processor coroutine)
        state = self.app.state  # type: ignore[attr-defined]
        state.permissions.reply(
            request_id=pending["request_id"],
            decision=decision,
            remember=remember,
            tool=pending["tool"],
        )

        # Re-freeze while waiting for the tool to complete
        self.query_one(InputBar).freeze()

    # ------------------------------------------------------------------
    # Core streaming worker
    # ------------------------------------------------------------------

    @work
    async def _stream_message(self, text: str, user_msg: "MessageInfo") -> None:
        """Run processor.run() and a concurrent bus subscriber together.

        The bus subscriber dispatches typed event objects directly — no JSON
        serialisation, no HTTP round-trips.
        """
        try:
            await self._do_stream(text, user_msg)
        except Exception as exc:
            self.post_message(events.StreamError(str(exc)))
            self.query_one(InputBar).enable()
            self._refresh_header()

    async def _do_stream(self, text: str, user_msg: "MessageInfo") -> None:
        state = self.app.state  # type: ignore[attr-defined]
        session = session_store.get(state.db, self._session_id)
        if not session:
            raise RuntimeError(f"Session {self._session_id!r} not found in DB")

        resolved_agent = agent_module.resolve(state.config)
        abort = asyncio.Event()

        input_bar = self.query_one(InputBar)
        self._refresh_header(streaming=True)

        subscribed = asyncio.Event()

        async def run_processor() -> None:
            success = False
            try:
                await subscribed.wait()  # don't publish until bus consumer is ready
                await state.processor.run(session, resolved_agent, text, abort, user_message=user_msg)
                success = True
            except Exception as exc:
                self.post_message(events.StreamError(str(exc)))
            finally:
                if not success:
                    # Ensure consume_bus can exit even when TurnCompleted wasn't published
                    await state.bus.publish(
                        TurnCompletedEvent(session_id=self._session_id, message_id="")
                    )

        async def consume_bus() -> None:
            async with state.bus.subscribe() as bus_events:
                subscribed.set()  # subscription is live — processor may now publish
                async for event in bus_events:
                    if getattr(event, "session_id", None) != self._session_id:
                        continue
                    self._dispatch_bus_event(event)
                    if isinstance(event, TurnCompletedEvent):
                        break

        try:
            await asyncio.gather(run_processor(), consume_bus())
        finally:
            input_bar.unfreeze()
            self._refresh_header(streaming=False)

    def _dispatch_bus_event(self, event: Any) -> None:
        """Translate a native bus event into a Textual message for widget handlers."""
        match event:
            case MessageCreatedEvent(message=msg) if msg:
                if not self.query_one(MessageList).get_message(msg.id):
                    self.post_message(events.NewMessage(msg.id, str(msg.role)))
                    # Only pre-populate text for roles with complete content upfront
                    # (user, error). Assistant text arrives via TextDeltaEvent stream.
                    if str(msg.role) != str(MessageRole.ASSISTANT):
                        for part in msg.parts or []:
                            if isinstance(part, TextPart) and part.content:
                                self.post_message(events.TextDelta(msg.id, part.content))
            case TextDeltaEvent(message_id=mid, content=c):
                self.post_message(events.TextDelta(mid, c))
            case ReasoningDeltaEvent(message_id=mid, content=c):
                self.post_message(events.ReasoningDelta(mid, c))
            case ToolStateChangedEvent(message_id=mid, part_index=idx, state=s):
                self.post_message(events.ToolStateChanged(mid, idx, s or {}))
            case TurnCompletedEvent(message_id=mid):
                self.post_message(events.TurnCompleted(mid))
            case BusPermissionRequested(request_id=rid, tool=t, description=d):
                self.post_message(events.PermissionRequested(rid, t, d, self._session_id))

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
            await widget.add_tool(event.part_index, event.state)
            msg_list.scroll_end(animate=False)

    @on(events.TurnCompleted)
    def handle_turn_completed(self, _event: events.TurnCompleted) -> None:
        self.query_one(InputBar).enable()
        self._refresh_header()

    @on(events.PermissionRequested)
    async def handle_permission_requested(self, event: events.PermissionRequested) -> None:
        state = self.app.state  # type: ignore[attr-defined]

        # Build descriptive message text
        description = event.description or f"run {event.tool}"
        msg_text = (
            f"⚠  [bold]{event.tool}[/bold]: {description}\n"
            f"[dim]1[/dim] allow  [dim]2[/dim] allow always  [dim]3[/dim] deny"
            f"   [dim](enter = 1)[/dim]"
        )

        # Persist and display as a permission message in the chat
        perm_msg = session_store.add_message(
            state.db,
            self._session_id,
            MessageRole.PERMISSION,
            [TextPart(content=msg_text)],
        )
        widget = await self.query_one(MessageList).add_message(
            perm_msg.id, str(MessageRole.PERMISSION)
        )
        widget.replace_text(msg_text)

        # Stop the spinner so the user can type their choice
        input_bar = self.query_one(InputBar)
        input_bar.unfreeze()
        input_bar.set_status(
            "[dim]1[/dim] allow  [dim]2[/dim] always  [dim]3[/dim] deny   [dim](enter = 1)[/dim]"
        )

        self._pending_permission = {
            "request_id": event.request_id,
            "tool": event.tool,
            "description": description,
            "message_id": perm_msg.id,
        }

    @on(events.StreamError)
    async def handle_stream_error(self, event: events.StreamError) -> None:
        await asyncio.sleep(3)  # TODO: remove — spinner test only
        state = self.app.state  # type: ignore[attr-defined]
        if state:
            error_msg = session_store.add_message(
                state.db,
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
        state = self.app.state  # type: ignore[attr-defined]
        project = project_module.get_or_create(state.db, state.project_dir)
        session = session_store.create(
            state.db,
            project_id=project.id,
            directory=str(state.project_dir),
        )
        self.app.push_screen(SessionScreen(session.id))

    def action_noop(self) -> None:
        pass
