"""Message list and message widgets."""

from __future__ import annotations

from typing import Any

from rich.markup import escape as _escape
from rich.syntax import Syntax as _Syntax

from openvibe.tui.markdown import render_markdown
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _looks_like_diff(text: str) -> bool:
    """Return True if the text looks like a unified diff."""
    for line in text.splitlines()[:10]:
        if line.startswith(("diff --git ", "--- ", "+++ ", "@@ ")):
            return True
    return False


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
        argument = self._extract_argument()
        arg_part = f" [dim]{_escape(argument)}[/dim]" if argument else ""
        return f"[{style}]{icon}[/{style}] [dim]{name}[/dim]{arg_part}"

    def _extract_argument(self) -> str:
        """Return a short display string for the primary tool argument."""
        inp: dict = self._state.get("input") or {}
        if not inp:
            return ""
        # Prefer well-known single-argument keys in priority order.
        for key in ("command", "path", "url", "query", "code", "input"):
            if key in inp:
                val = str(inp[key])
                if len(val) > 200:
                    val = val[:200] + "…"
                return val
        # Fall back to the first value.
        val = str(next(iter(inp.values())))
        if len(val) > 200:
            val = val[:200] + "…"
        return val

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
        out = self.query_one("#output", Static)
        if not self._expanded:
            out.update("")
            return
        content = self._state.get("output") or self._state.get("error") or ""
        if len(content) > 2000:
            content = content[:2000] + "\n… (truncated)"
        if _looks_like_diff(content):
            out.update(_Syntax(content, "diff", theme="monokai", word_wrap=True))
        else:
            out.update(f"[dim]{_escape(content)}[/dim]")


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
    MessageWidget Static.hidden-text {
        display: none;
    }
    MessageWidget.user {
        background: #3A3A3A;
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
        # Start hidden — revealed on first text so tool-only assistant messages
        # don't render a blank line before the tool widgets.
        yield Static("", id=f"text-{self._message_id}", classes="hidden-text")

    def append_text(self, content: str) -> None:
        widget = self.query_one(f"#text-{self._message_id}", Static)
        widget.remove_class("hidden-text")
        if self._role == "assistant":
            self._text += content
            widget.update(render_markdown(self._text))
        else:
            safe = _escape(content)
            if not self._text and self._role == "user":
                self._text = f"[dim]>[/dim] {safe}"
            elif not self._text and self._role == "error":
                self._text = f"⚠  {safe}"
            else:
                self._text += safe
            widget.update(self._text)

    def replace_text(self, content: str) -> None:
        self._text = content
        widget = self.query_one(f"#text-{self._message_id}", Static)
        widget.remove_class("hidden-text")
        if self._role == "assistant":
            widget.update(render_markdown(self._text))
        else:
            widget.update(self._text)

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
