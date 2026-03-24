"""Session and message data models.

All models are Pydantic ``BaseModel`` instances so they validate on construction
and serialise cleanly to/from JSON (stored in the ``parts`` table).

Message anatomy
---------------
A ``Message`` represents one turn in a conversation:

    UserMessage
        └─ parts: list[MessagePart]  (usually just one TextPart)

    AssistantMessage
        └─ parts: list[MessagePart]  (TextPart, ToolPart, ReasoningPart, …)

Part types use Pydantic discriminated unions (``type`` field) so
``model_validate`` can reconstruct the correct subtype from raw JSON.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from openvibe.config import MessageRole, ToolStateStatus

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class SessionInfo(BaseModel):
    id: str
    project_id: str
    slug: str
    title: str | None = None
    parent_id: str | None = None
    directory: str
    version: int = 1
    created_at: str
    updated_at: str
    archived_at: str | None = None

    # Lightweight usage summary updated after each turn
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    # Session-level config overrides (JSON blob, nullable).
    # Merged on top of the base project config when the Session is loaded.
    config_json: str | None = None


# ---------------------------------------------------------------------------
# Part types (discriminated union on `type`)
# ---------------------------------------------------------------------------


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    content: str = ""
    # Timing metadata (seconds since message start)
    time_start: float | None = None
    time_end: float | None = None


class ReasoningPart(BaseModel):
    type: Literal["reasoning"] = "reasoning"
    content: str = ""
    time_start: float | None = None
    time_end: float | None = None


class ToolState(BaseModel):
    """State machine for a single tool invocation."""

    status: ToolStateStatus = ToolStateStatus.PENDING
    # Populated when the call starts
    call_id: str = ""
    tool_name: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    # Populated when the call finishes
    output: str | None = None
    error: str | None = None
    time_start: float | None = None
    time_end: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolPart(BaseModel):
    type: Literal["tool"] = "tool"
    state: ToolState = Field(default_factory=ToolState)


class StepStartPart(BaseModel):
    """Marker inserted at the beginning of each agent iteration."""

    type: Literal["step_start"] = "step_start"


class CompactionPart(BaseModel):
    """Placeholder for a range of messages that have been summarised."""

    type: Literal["compaction"] = "compaction"
    summary: str = ""
    message_count: int = 0


MessagePart = Annotated[
    TextPart | ReasoningPart | ToolPart | StepStartPart | CompactionPart,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class MessageInfo(BaseModel):
    id: str
    session_id: str
    role: MessageRole
    position: int
    created_at: str
    parts: list[MessagePart] = Field(default_factory=list)

    # Assistant-message metadata (populated after the turn completes)
    model: str | None = None
    provider: str | None = None
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    error: AssistantError | None = None


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class APIError(BaseModel):
    type: Literal["api_error"] = "api_error"
    message: str
    status_code: int | None = None


class ContextOverflowError(BaseModel):
    type: Literal["context_overflow"] = "context_overflow"
    message: str = "Context window exceeded. Compaction required."


class AuthError(BaseModel):
    type: Literal["auth_error"] = "auth_error"
    message: str
    provider: str | None = None


class OutputLengthError(BaseModel):
    type: Literal["output_length_error"] = "output_length_error"
    message: str = "Response exceeded the maximum output length."


class AbortedError(BaseModel):
    type: Literal["aborted"] = "aborted"
    message: str = "Request was cancelled."


AssistantError = Annotated[
    APIError | ContextOverflowError | AuthError | OutputLengthError | AbortedError,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Bus events (published by the session processor)
# ---------------------------------------------------------------------------

from dataclasses import dataclass

from openvibe.bus import Event


@dataclass
class SessionCreatedEvent(Event):
    session: SessionInfo | None = None


@dataclass
class SessionUpdatedEvent(Event):
    session: SessionInfo | None = None


@dataclass
class MessageCreatedEvent(Event):
    message: MessageInfo | None = None


@dataclass
class PartUpdatedEvent(Event):
    message_id: str = ""
    part_index: int = 0
    part: dict[str, Any] | None = None  # serialised MessagePart


@dataclass
class TextDeltaEvent(Event):
    message_id: str = ""
    content: str = ""


@dataclass
class ReasoningDeltaEvent(Event):
    message_id: str = ""
    content: str = ""


@dataclass
class ToolStateChangedEvent(Event):
    message_id: str = ""
    part_index: int = 0
    state: dict[str, Any] | None = None  # serialised ToolState


@dataclass
class TurnCompletedEvent(Event):
    message_id: str = ""
    stop_reason: str = "end_turn"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
