"""Textual application for openvibe.

The TUI uses the public ``OpenVibe`` API exclusively.  ``start_async()`` is
called on mount so the full async stack (EventBus, SessionProcessor,
PermissionService) is available to sessions created from this instance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual import work  # noqa: F401 – used via @work decorator
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer

from openvibe.api import OpenVibe

_CSS = """
Screen {
    background: #000000;
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
        # Cache pending-permission dicts keyed by session_id so they survive
        # screen re-mounts (the request_id must be preserved for the async
        # PermissionService to resolve its Future correctly).
        self._pending_permissions: dict[str, Any] = {}
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Footer()

    async def on_mount(self) -> None:
        if _needs_setup(self._project_dir):
            from openvibe.tui.screens.setup import SetupWizardScreen

            self.push_screen(SetupWizardScreen(), callback=self._after_wizard)
        else:
            self._finish_startup()

    def _after_wizard(self, _: None) -> None:
        """Called when the setup wizard is dismissed (saved or skipped)."""
        self._finish_startup()

    @work
    async def _finish_startup(self) -> None:
        self.ov = await OpenVibe(project_dir=self._project_dir).start_async()
        from openvibe.tui.screens.welcome import WelcomeScreen

        await self.push_screen(WelcomeScreen())

    async def on_unmount(self) -> None:
        # Abort any in-flight sessions so their worker threads (and the
        # run_in_executor threads they spawned) can exit cleanly before the
        # ThreadPoolExecutor atexit handler tries to join them.
        for session in self._session_cache.values():
            try:
                session.abort(timeout=0)
            except Exception:  # noqa: BLE001
                pass
        if self.ov is not None:
            await self.ov.close_async()

    def get_session(self, session_id: str) -> Any:
        """Return a cached Session object, loading it from the DB if needed."""
        if session_id not in self._session_cache:
            assert self.ov is not None
            self._session_cache[session_id] = self.ov.get_session(session_id)
        return self._session_cache[session_id]

    def set_pending_permission(self, session_id: str, pending: Any) -> None:
        """Store or clear the pending-permission dict for a session."""
        if pending is None:
            self._pending_permissions.pop(session_id, None)
        else:
            self._pending_permissions[session_id] = pending

    def get_pending_permission(self, session_id: str) -> Any:
        """Return the pending-permission dict for a session, or None."""
        return self._pending_permissions.get(session_id)

    def action_sessions(self) -> None:
        from openvibe.tui.screens.session import SessionScreen
        from openvibe.tui.screens.sessions import SessionListScreen

        def on_dismiss(session_id: str | None) -> None:
            if session_id:
                self.push_screen(SessionScreen(session_id))

        self.push_screen(SessionListScreen(), on_dismiss)


def _needs_setup(project_dir: Path) -> bool:
    """Return True if no model is configured from any config source."""
    from openvibe.config import load_config

    try:
        return load_config(project_dir).model is None
    except Exception:
        return True


def run_tui(project_dir: Path | None = None) -> None:
    """Entry point: launch the TUI with an embedded in-process backend."""
    OpenvibeApp(project_dir=project_dir or Path.cwd()).run()
