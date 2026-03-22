"""Session list / picker screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static


class SessionListScreen(Screen[str | None]):
    """Full-screen session picker.

    Dismisses with the selected session ID, or ``None`` if cancelled.
    Creating a new session dismisses with the new session's ID.
    """

    DEFAULT_CSS = """
    SessionListScreen {
        align: center middle;
    }
    #dialog {
        width: 70;
        max-height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    #title {
        text-style: bold;
        margin-bottom: 1;
        color: $primary;
    }
    #filter {
        margin-bottom: 1;
    }
    #session-list {
        height: 1fr;
        max-height: 20;
        border: solid $border;
        margin-bottom: 1;
    }
    #empty {
        color: $text-muted;
        text-align: center;
        padding: 2 0;
    }
    #buttons {
        height: auto;
        margin-top: 1;
    }
    #buttons Button {
        margin-right: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_none", "Back", show=True),
        Binding("ctrl+n", "new_session", "New session", show=True),
    ]

    def __init__(self) -> None:
        self._all_sessions: list = []
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("[bold]openvibe[/bold] — sessions", id="title")
            yield Input(placeholder="Filter sessions…", id="filter")
            yield ListView(id="session-list")
            yield Static("No sessions yet.", id="empty")
            with Horizontal(id="buttons"):
                yield Button("New Session", id="new-session", variant="primary")
                yield Button("Cancel", id="cancel", variant="default")

    def on_mount(self) -> None:
        ov = self.app.ov  # type: ignore[attr-defined]
        self._all_sessions = ov.list_sessions()
        self._populate(self._all_sessions)

    def _populate(self, sessions: list) -> None:
        lv = self.query_one("#session-list", ListView)
        lv.clear()
        empty = self.query_one("#empty", Static)

        if not sessions:
            empty.display = True
            return

        empty.display = False
        for s in sessions:
            title = s.title or f"session {s.id[:8]}"
            updated = (s.updated_at or "")[:10]
            lv.append(
                ListItem(
                    Label(f"{title}  [dim]{updated}[/dim]"),
                    id=f"s-{s.id}",
                )
            )

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        q = event.value.lower()
        filtered = (
            [
                s
                for s in self._all_sessions
                if q in (s.title or "").lower() or q in s.id.lower()
            ]
            if q
            else self._all_sessions
        )
        self._populate(filtered)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id:
            session_id = event.item.id[2:]  # strip "s-" prefix
            self.dismiss(session_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "new-session":
            self.action_new_session()
        elif event.button.id == "cancel":
            self.dismiss(None)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_new_session(self) -> None:
        ov = self.app.ov  # type: ignore[attr-defined]
        session = ov.create_session()
        self.dismiss(session.id)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)
