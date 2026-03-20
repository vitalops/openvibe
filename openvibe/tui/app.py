"""Textual application for openvibe.

The TUI uses the public ``OpenVibe`` API exclusively.  ``start_async()`` is
called on mount so the full async stack (EventBus, SessionProcessor,
PermissionService) is available to sessions created from this instance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer

from openvibe.api import OpenVibe


_CSS = """
Screen {
    background: $background;
}
"""


class OpenvibeApp(App[None]):
    """TUI that operates via the public OpenVibe API."""

    TITLE = "openvibe"
    CSS = _CSS

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+s", "sessions", "Sessions", show=True),
    ]

    def __init__(self, project_dir: Path | None = None) -> None:
        self._project_dir = project_dir or Path.cwd()
        self.ov: OpenVibe | None = None
        # Cache Session objects so state is preserved across screen pushes/pops.
        self._session_cache: dict[str, Any] = {}
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Footer()

    async def on_mount(self) -> None:
        self.ov = await OpenVibe(project_dir=self._project_dir).start_async()
        from openvibe.tui.screens.welcome import WelcomeScreen
        await self.push_screen(WelcomeScreen())

    async def on_unmount(self) -> None:
        if self.ov is not None:
            await self.ov.close_async()

    def get_session(self, session_id: str) -> Any:
        """Return a cached Session object, loading it from the DB if needed."""
        if session_id not in self._session_cache:
            assert self.ov is not None
            self._session_cache[session_id] = self.ov.get_session(session_id)
        return self._session_cache[session_id]

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
