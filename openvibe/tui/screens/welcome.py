"""Welcome screen — the first thing the user sees when they run `vibe`."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.widgets import Button, Label, ListItem, ListView, Static

from openvibe import __version__


_LOGO = r"""
 ██╗   ██╗██╗██████╗ ███████╗
 ██║   ██║██║██╔══██╗██╔════╝
 ██║   ██║██║██████╔╝█████╗
 ╚██╗ ██╔╝██║██╔══██╗██╔══╝
  ╚████╔╝ ██║██████╔╝███████╗
   ╚═══╝  ╚═╝╚═════╝ ╚══════╝"""

_TAGLINE = "AI coding agent for your terminal"

_HELP = """\
[bold]Ctrl+N[/bold]  new session    \
[bold]Ctrl+S[/bold]  sessions    \
[bold]Ctrl+Q[/bold]  quit\
"""


class WelcomeScreen(Screen[None]):
    """Splash / home screen shown on first launch."""

    DEFAULT_CSS = """
    WelcomeScreen {
        align: center middle;
        background: $background;
    }
    #panel {
        width: 64;
        height: auto;
        border: round $primary;
        background: $surface;
        padding: 1 3;
    }
    #logo {
        color: $primary;
        text-align: center;
        margin-bottom: 0;
    }
    #tagline {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }
    #version {
        text-align: center;
        color: $text-disabled;
        margin-bottom: 1;
    }
    #divider {
        border-bottom: solid $border;
        margin-bottom: 1;
        height: 1;
    }
    #help {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }
    #recent-label {
        color: $text-muted;
        margin-bottom: 0;
    }
    #recent-list {
        height: auto;
        max-height: 6;
        border: solid $border;
        margin-bottom: 1;
    }
    #no-recent {
        color: $text-disabled;
        text-align: center;
        padding: 0 0 1 0;
    }
    #actions {
        height: auto;
        margin-top: 1;
        align-horizontal: center;
    }
    #actions Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+n", "new_session", "New session", show=False),
        Binding("ctrl+s", "all_sessions", "All sessions", show=False),
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("q", "quit", "Quit", show=False),
    ]

    def __init__(self) -> None:
        self._recent: list = []
        super().__init__()

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="panel"):
                yield Static(_LOGO, id="logo")
                yield Static(_TAGLINE, id="tagline")
                yield Static(f"v{__version__}", id="version")
                yield Static("", id="divider")
                yield Static(_HELP, id="help")
                yield Static("Recent sessions", id="recent-label")
                yield ListView(id="recent-list")
                yield Static("No sessions yet — start one below.", id="no-recent")
                with Center(id="actions"):
                    yield Button("New Session", id="new", variant="primary")
                    yield Button("All Sessions", id="all", variant="default")
                    yield Button("Quit", id="quit-btn", variant="error")

    def on_mount(self) -> None:
        ov = self.app.ov  # type: ignore[attr-defined]
        self._recent = ov.list_sessions()[:5]
        self._populate_recent()

    def _populate_recent(self) -> None:
        lv = self.query_one("#recent-list", ListView)
        no_recent = self.query_one("#no-recent", Static)

        if not self._recent:
            lv.display = False
            no_recent.display = True
            return

        no_recent.display = False
        lv.display = True
        for s in self._recent:
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

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id:
            session_id = event.item.id[2:]  # strip "s-"
            self._open_session(session_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        match event.button.id:
            case "new":
                self.action_new_session()
            case "all":
                self.action_all_sessions()
            case "quit-btn":
                self.app.exit()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_new_session(self) -> None:
        ov = self.app.ov  # type: ignore[attr-defined]
        session = ov.create_session()
        self._open_session(session.id)

    def action_all_sessions(self) -> None:
        from openvibe.tui.screens.sessions import SessionListScreen
        from openvibe.tui.screens.session import SessionScreen

        def on_dismiss(session_id: str | None) -> None:
            if session_id:
                self._open_session(session_id)

        self.app.push_screen(SessionListScreen(), on_dismiss)

    def _open_session(self, session_id: str) -> None:
        from openvibe.tui.screens.session import SessionScreen
        self.app.push_screen(SessionScreen(session_id))
