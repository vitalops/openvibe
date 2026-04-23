"""Input bar widget with history navigation."""

from __future__ import annotations

import time
from typing import Any

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static, TextArea

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class PermButton(Static):
    """Clickable permission button built on Static — no hidden inner label."""

    can_focus = False

    class Clicked(Message):
        BUBBLE = True

        def __init__(self, button_id: str) -> None:
            self.button_id = button_id
            super().__init__()

    def on_click(self) -> None:
        self.post_message(self.Clicked(self.id or ""))


class ChatInput(TextArea):
    """TextArea that submits on Enter and inserts a newline on Shift+Enter."""

    DEFAULT_CSS = """
    ChatInput {
        height: auto;
        max-height: 6;
        border: none;
        background: transparent;
        padding: 0;
    }
    ChatInput:focus {
        border: none;
        outline: none;
    }
    """

    BINDINGS = [
        Binding("ctrl+j", "newline", "New line", priority=True, show=False),
        Binding("up", "history_prev", "Previous", show=False),
        Binding("down", "history_next", "Next", show=False),
    ]

    def __init__(self, **kwargs: Any) -> None:
        self._submittable = True
        super().__init__(**kwargs)

    async def _on_key(self, event: events.Key) -> None:
        """Intercept Enter to submit."""
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            if not self._submittable:
                return
            text = self.text.strip()
            if text:
                self.post_message(InputBar.Submitted(text))
                self.load_text("")
        else:
            await super()._on_key(event)

    def action_newline(self) -> None:
        """Shift+Enter: insert a real newline via a priority binding."""
        self.insert("\n")

    def on_blur(self) -> None:
        """Reclaim focus unless we're disabled (e.g. permission bar is active)."""
        if not self.disabled:
            self.focus()

    def action_history_prev(self) -> None:
        row, _ = self.cursor_location
        if row == 0:
            self.post_message(InputBar.HistoryNav(direction=-1))
        else:
            self.action_cursor_up()

    def action_history_next(self) -> None:
        row, _ = self.cursor_location
        last_row = self.document.line_count - 1
        if row >= last_row:
            self.post_message(InputBar.HistoryNav(direction=1))
        else:
            self.action_cursor_down()


class InputBar(Widget):
    """Bottom input area: a ChatInput plus a status/hints row."""

    DEFAULT_CSS = """
    InputBar {
        height: auto;
        max-height: 9;
        border-top: solid $surface;
        padding: 0 1;
    }
    InputBar #status {
        height: 1;
        color: $text-disabled;
    }
    InputBar #status.hidden {
        display: none;
    }
    InputBar #perm-bar {
        height: 1;
        display: none;
    }
    InputBar #perm-bar.visible {
        display: block;
    }
    InputBar .perm-btn {
        height: 1;
        width: auto;
        padding: 0 1;
        margin: 0 1 0 0;
        color: white;
    }
    InputBar .perm-btn:hover {
        text-style: bold;
    }
    InputBar #btn-allow {
        background: green;
    }
    InputBar #btn-allow:hover {
        background: #55ff55;
        color: black;
    }
    InputBar #btn-always {
        background: $surface-lighten-2;
    }
    InputBar #btn-always:hover {
        background: $surface-lighten-3;
    }
    InputBar #btn-deny {
        background: red;
    }
    InputBar #btn-deny:hover {
        background: #ff5555;
        color: black;
    }
    InputBar #perm-hint {
        color: $text-disabled;
        width: auto;
        padding: 0 0 0 1;
    }
    InputBar ChatInput {
        margin-top: 0;
    }
    """

    # ------------------------------------------------------------------
    # Nested messages
    # ------------------------------------------------------------------

    class Submitted(Message):
        BUBBLE = True

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    class HistoryNav(Message):
        BUBBLE = True

        def __init__(self, direction: int) -> None:
            self.direction = direction
            super().__init__()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(self, **kwargs: Any) -> None:
        self._history: list[str] = []
        self._history_idx: int = -1
        self._draft: str = ""
        self._spinner_frame: int = 0
        self._spinner_timer: Timer | None = None
        self._thinking_start: float = 0.0
        self._permission_mode: bool = False
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield Static(self._hint_text(), id="status")
        with Horizontal(id="perm-bar"):
            yield PermButton("1 allow", id="btn-allow", classes="perm-btn")
            yield PermButton("2 always", id="btn-always", classes="perm-btn")
            yield PermButton("3 deny", id="btn-deny", classes="perm-btn")
            yield Static("[dim](enter = 1)[/dim]", id="perm-hint")
        yield ChatInput(id="chat-input")

    def _hint_text(self) -> str:
        return "[dim]enter[/dim] send  [dim]ctrl+j[/dim] newline  [dim]↑↓[/dim] history  [dim]ctrl+y[/dim] copy  [dim]ctrl+q[/dim] exit"

    def focus_input(self) -> None:
        self.query_one(ChatInput).focus()

    # ------------------------------------------------------------------
    # Permission mode (single-keypress input: 1/2/3/enter)
    # ------------------------------------------------------------------

    def enter_permission_mode(self) -> None:
        self._stop_spinner()
        self._permission_mode = True
        ci = self.query_one(ChatInput)
        ci._submittable = False
        ci.disabled = True
        self.query_one("#status", Static).add_class("hidden")
        self.query_one("#perm-bar").add_class("visible")

    def exit_permission_mode(self) -> None:
        self._permission_mode = False
        ci = self.query_one(ChatInput)
        ci.disabled = False
        self.query_one("#perm-bar").remove_class("visible")
        self.query_one("#status", Static).remove_class("hidden")

    @property
    def in_permission_mode(self) -> bool:
        return self._permission_mode

    def on_perm_button_clicked(self, event: PermButton.Clicked) -> None:
        if not self._permission_mode:
            return
        match event.button_id:
            case "btn-allow":
                self.post_message(self.Submitted("1"))
            case "btn-always":
                self.post_message(self.Submitted("2"))
            case "btn-deny":
                self.post_message(self.Submitted("3"))

    # ------------------------------------------------------------------
    # Freeze / unfreeze (spinner in status row, input stays visible)
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_elapsed(seconds: int) -> str:
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m{seconds % 60}s"
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}h{m}m{s}s"

    def freeze(self) -> None:
        self.query_one(ChatInput)._submittable = False
        self._spinner_frame = 0
        self._thinking_start = time.monotonic()
        self._tick_spinner()
        self._spinner_timer = self.set_interval(0.08, self._tick_spinner)

    def _tick_spinner(self) -> None:
        frame = _SPINNER_FRAMES[self._spinner_frame % len(_SPINNER_FRAMES)]
        elapsed = self._fmt_elapsed(int(time.monotonic() - self._thinking_start))
        self.query_one("#status", Static).update(
            f"[dim]{frame} thinking… {elapsed}[/dim]"
        )
        self._spinner_frame += 1

    def _stop_spinner(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self.query_one("#status", Static).update(self._hint_text())

    def unfreeze(self) -> None:
        self._stop_spinner()
        ci = self.query_one(ChatInput)
        ci._submittable = True
        ci.focus()

    def set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def disable(self) -> None:
        self.query_one(ChatInput).disabled = True

    def enable(self) -> None:
        self._stop_spinner()
        ci = self.query_one(ChatInput)
        ci._submittable = True
        ci.disabled = False
        ci.focus()

    # ------------------------------------------------------------------
    # History navigation
    # ------------------------------------------------------------------

    def navigate_history(self, direction: int) -> None:
        if not self._history:
            return
        ci = self.query_one(ChatInput)
        if self._history_idx == -1:
            self._draft = ci.text

        self._history_idx = max(
            -1,
            min(len(self._history) - 1, self._history_idx + direction),
        )

        if self._history_idx == -1:
            ci.load_text(self._draft)
        else:
            entry = self._history[-(self._history_idx + 1)]
            ci.load_text(entry)

        ci.move_cursor(ci.document.end)

    def record_submission(self, text: str) -> None:
        if not self._history or self._history[-1] != text:
            self._history.append(text)
        self._history_idx = -1
        self._draft = ""
