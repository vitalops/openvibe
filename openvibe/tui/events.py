"""Textual Message subclasses that carry server-sent event data into the widget tree.

The SSE worker in SessionScreen translates raw JSON payloads into these typed
messages and posts them via ``self.post_message()``.  Widgets handle them
with ``@on(events.TextDelta)`` etc.

All messages have ``BUBBLE = False`` so they are consumed by the first
matching ``@on`` handler and do not propagate further up the tree.
"""

from __future__ import annotations

from typing import Any

from textual.message import Message


class NewMessage(Message):
    """A new message row was created (user or assistant shell)."""

    BUBBLE = False

    def __init__(self, message_id: str, role: str) -> None:
        self.message_id = message_id
        self.role = role
        super().__init__()


class TextDelta(Message):
    """Incremental text content from the assistant."""

    BUBBLE = False

    def __init__(self, message_id: str, content: str) -> None:
        self.message_id = message_id
        self.content = content
        super().__init__()


class ReasoningDelta(Message):
    """Incremental reasoning/thinking content."""

    BUBBLE = False

    def __init__(self, message_id: str, content: str) -> None:
        self.message_id = message_id
        self.content = content
        super().__init__()


class ToolStateChanged(Message):
    """A tool call's status or output changed."""

    BUBBLE = False

    def __init__(
        self,
        message_id: str,
        part_index: int,
        state: dict[str, Any],
    ) -> None:
        self.message_id = message_id
        self.part_index = part_index
        self.state = state
        super().__init__()


class TurnCompleted(Message):
    """The assistant finished its turn."""

    BUBBLE = False

    def __init__(self, message_id: str) -> None:
        self.message_id = message_id
        super().__init__()


class PermissionRequested(Message):
    """The agent needs user approval before running a tool."""

    BUBBLE = False

    def __init__(
        self,
        request_id: str,
        tool: str,
        description: str,
        session_id: str,
        argument: str | None = None,
    ) -> None:
        self.request_id = request_id
        self.tool = tool
        self.description = description
        self.session_id = session_id
        self.argument = argument
        super().__init__()


class PermissionResolved(Message):
    """The user replied to a permission request."""

    BUBBLE = False


class StreamError(Message):
    """An error occurred during SSE streaming."""

    BUBBLE = False

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__()
