"""Textual application for openvibe.

In the default (direct) mode the TUI constructs an ``AppState`` and talks to
the core Python objects directly — no HTTP server, no port binding.

A ``--url`` flag is available for connecting to a *remote* openvibe server
instead; that path keeps ``OpenvibeClient`` and is the only case where the
HTTP layer is used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer

from openvibe.core import AppState, create_app_state


_CSS = """
Screen {
    background: $background;
}
"""


class OpenvibeApp(App[None]):
    """TUI that operates directly on the core Python objects."""

    TITLE = "openvibe"
    CSS = _CSS

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+s", "sessions", "Sessions", show=True),
    ]

    def __init__(self, project_dir: Path | None = None) -> None:
        self._project_dir = project_dir or Path.cwd()
        # Set after on_mount; screens access this via self.app.state
        self.state: AppState | None = None
        self._state_cm: Any = None
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Footer()

    async def on_mount(self) -> None:
        self._state_cm = create_app_state(project_dir=self._project_dir)
        self.state = await self._state_cm.__aenter__()
        from openvibe.tui.screens.welcome import WelcomeScreen
        await self.push_screen(WelcomeScreen())

    async def on_unmount(self) -> None:
        if self._state_cm is not None:
            await self._state_cm.__aexit__(None, None, None)

    def action_sessions(self) -> None:
        from openvibe.tui.screens.sessions import SessionListScreen
        from openvibe.tui.screens.session import SessionScreen

        def on_dismiss(session_id: str | None) -> None:
            if session_id:
                self.push_screen(SessionScreen(session_id))

        self.push_screen(SessionListScreen(), on_dismiss)


def run_tui(project_dir: Path | None = None) -> None:
    """Entry point: launch the TUI with an embedded in-process backend."""
    OpenvibeApp(project_dir=project_dir or Path.cwd()).run()
