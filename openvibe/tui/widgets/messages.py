"""Message list and message widgets."""

from __future__ import annotations

from typing import Any

from rich.markup import escape as _escape
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static


# ---------------------------------------------------------------------------
# Tool widget
# ---------------------------------------------------------------------------

_STATUS_ICON: dict[str, str] = {
    "pending":   "○",
    "running":   "◎",
    "completed": "●",
    "error":     "✗",
}

_STATUS_STYLE: dict[str, str] = {
    "pending":   "dim",
    "running":   "yellow",
    "completed": "green",
    "error":     "red",
}


class ToolWidget(Widget):
    """Renders one tool call: status icon + name, expandable output on click."""

    DEFAULT_CSS = """
    ToolWidget {
        height: auto;
        padding: 0 0 0 2;
        color: $text-muted;
    }
    ToolWidget .output {
        height: auto;
        padding-left: 4;
        color: $text-muted;
    }
    ToolWidget Static {
        height: auto;
    }
    """

    def __init__(self, state: dict[str, Any], **kwargs: Any) -> None:
        self._state = state
        self._expanded = False
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield Static(self._header_markup(), id="header")
        yield Static("", classes="output", id="output")

    def _header_markup(self) -> str:
        status = self._state.get("status", "pending")
        icon = _STATUS_ICON.get(status, "?")
        style = _STATUS_STYLE.get(status, "white")
        name = self._state.get("tool_name", "unknown")
        return f"[{style}]{icon}[/{style}] [dim]{name}[/dim]"

    def update_state(self, state: dict[str, Any]) -> None:
        self._state = state
        self.query_one("#header", Static).update(self._header_markup())
        if self._expanded:
            self._refresh_output()

    def on_click(self) -> None:
        if self._state.get("output") or self._state.get("error"):
            self._expanded = not self._expanded
            self._refresh_output()

    def _refresh_output(self) -> None:
        text = ""
        if self._expanded:
            content = self._state.get("output") or self._state.get("error") or ""
            if len(content) > 2000:
                content = content[:2000] + "\n… (truncated)"
            text = f"[dim]{_escape(content)}[/dim]"
        self.query_one("#output", Static).update(text)


# ---------------------------------------------------------------------------
# Single message widget
# ---------------------------------------------------------------------------

class MessageWidget(Widget):
    """Renders one message: streaming text and optional tool parts."""

    DEFAULT_CSS = """
    MessageWidget {
        height: auto;
        padding: 0 2;
        margin: 0;
    }
    MessageWidget Static {
        height: auto;
    }
    MessageWidget.user {
        background: $surface;
        padding: 0 2;
    }
    MessageWidget.error {
        background: $error 8%;
        padding: 0 2;
        color: $error;
    }
    MessageWidget.permission {
        background: $warning 10%;
        padding: 0 2;
        color: $warning;
    }
    """

    def __init__(self, message_id: str, role: str, **kwargs: Any) -> None:
        self._message_id = message_id
        self._role = role
        self._text = ""
        self._tools: dict[int, ToolWidget] = {}
        super().__init__(classes=role, **kwargs)

    def compose(self) -> ComposeResult:
        yield Static("", id=f"text-{self._message_id}")

    def append_text(self, content: str) -> None:
        safe = _escape(content)
        if not self._text and self._role == "user":
            self._text = f"[dim]>[/dim] {safe}"
        elif not self._text and self._role == "error":
            self._text = f"⚠  {safe}"
        else:
            self._text += safe
        self.query_one(f"#text-{self._message_id}", Static).update(self._text)

    def replace_text(self, content: str) -> None:
        self._text = content
        self.query_one(f"#text-{self._message_id}", Static).update(self._text)

    async def add_tool(self, index: int, state: dict[str, Any]) -> None:
        tool = ToolWidget(state, id=f"tool-{self._message_id}-{index}")
        self._tools[index] = tool
        await self.mount(tool)

    def update_tool(self, index: int, state: dict[str, Any]) -> None:
        if tool := self._tools.get(index):
            tool.update_state(state)


# ---------------------------------------------------------------------------
# Message list (scrollable container)
# ---------------------------------------------------------------------------

class MessageList(VerticalScroll):
    """Scrollable list of MessageWidgets; receives updates from SessionScreen."""

    DEFAULT_CSS = """
    MessageList {
        height: 1fr;
        padding: 0;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        self._messages: dict[str, MessageWidget] = {}
        super().__init__(**kwargs)

    async def add_message(self, message_id: str, role: str) -> MessageWidget:
        if message_id in self._messages:
            return self._messages[message_id]
        widget = MessageWidget(message_id, role, id=f"msg-{message_id}")
        self._messages[message_id] = widget
        await self.mount(widget)
        self.scroll_end(animate=False)
        return widget

    def get_message(self, message_id: str) -> MessageWidget | None:
        return self._messages.get(message_id)

    def append_text(self, message_id: str, content: str) -> None:
        if widget := self._messages.get(message_id):
            widget.append_text(content)
            self.scroll_end(animate=False)
