"""Permission request bar — keyboard-driven, no mouse required."""

from __future__ import annotations

from typing import Any

from textual import events
from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static


class PermissionBar(Widget):
    """Displays a tool-permission prompt with numbered keyboard options.

    Press Enter or 1 → Allow (default)
          2          → Allow Always
          3          → Deny
    """

    DEFAULT_CSS = """
    PermissionBar {
        height: auto;
        background: $surface;
        color: $text;
        padding: 0 2;
        display: none;
        border-top: solid $warning;
    }
    PermissionBar.active {
        display: block;
    }
    PermissionBar #description {
        color: $warning;
    }
    PermissionBar #options {
        color: $text-muted;
    }
    """

    class Replied(Message):
        BUBBLE = True

        def __init__(
            self,
            request_id: str,
            tool: str,
            decision: str,  # "allow" | "deny"
            remember: bool,
        ) -> None:
            self.request_id = request_id
            self.tool = tool
            self.decision = decision
            self.remember = remember
            super().__init__()

    def __init__(self, **kwargs: Any) -> None:
        self._request_id = ""
        self._tool = ""
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield Static("", id="description")
        yield Static(
            "[dim]1[/dim] allow  [dim]2[/dim] allow always  [dim]3[/dim] deny"
            "   [dim](enter = 1)[/dim]",
            id="options",
        )

    # ------------------------------------------------------------------
    # Show / hide
    # ------------------------------------------------------------------

    def show_request(self, request_id: str, tool: str, description: str) -> None:
        self._request_id = request_id
        self._tool = tool
        label = description or f"run [bold]{tool}[/bold]"
        self.query_one("#description", Static).update(f"⚠  {label}")
        self.add_class("active")
        self.focus()

    def hide(self) -> None:
        self.remove_class("active")

    # ------------------------------------------------------------------
    # Keyboard handler
    # ------------------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        match event.key:
            case "enter" | "1":
                event.stop()
                self._reply("allow", remember=False)
            case "2":
                event.stop()
                self._reply("allow", remember=True)
            case "3":
                event.stop()
                self._reply("deny", remember=False)

    def _reply(self, decision: str, *, remember: bool) -> None:
        self.post_message(
            PermissionBar.Replied(
                request_id=self._request_id,
                tool=self._tool,
                decision=decision,
                remember=remember,
            )
        )
        self.hide()
