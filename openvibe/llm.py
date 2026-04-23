"""LLM abstraction layer.

All LLM calls go through this module. The concrete ``LiteLLMBackend``
delegates to the ``litellm`` library which supports Anthropic, OpenAI,
Google, Bedrock, Azure, Mistral, and ~100 other providers.

Swapping backends
-----------------
Implement the ``LLMBackend`` protocol::

    class MyBackend:
        async def stream(
            self,
            model: str,
            messages: list[Message],
            tools: list[ToolDefinition] | None = None,
            **kwargs: Any,
        ) -> AsyncIterator[LLMEvent]:
            ...

Then pass it to ``create_app(llm=MyBackend())``.

Event stream contract
---------------------
``stream()`` must yield ``LLMEvent`` objects. The caller accumulates deltas
and handles tool execution after ``ToolCallComplete`` is received.

    TextDelta*          — zero or more incremental text fragments
    ReasoningDelta*     — optional reasoning/thinking tokens
    ToolCallBegin       — opening of a tool invocation
    ToolCallDelta*      — argument JSON fragments for that tool
    ToolCallComplete    — complete, ready-to-execute tool call
    StreamDone          — end of the model's turn; carries usage data
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol
from openvibe.config import load_config
import litellm

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


@dataclass
class TextDelta:
    content: str


@dataclass
class ReasoningDelta:
    content: str


@dataclass
class ToolCallBegin:
    index: int
    id: str
    name: str


@dataclass
class ToolCallDelta:
    index: int
    args_delta: str


@dataclass
class ToolCallComplete:
    index: int
    id: str
    name: str
    arguments: str  # raw JSON string


@dataclass
class StreamDone:
    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


LLMEvent = (
    TextDelta
    | ReasoningDelta
    | ToolCallBegin
    | ToolCallDelta
    | ToolCallComplete
    | StreamDone
)


# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


@dataclass
class ContentBlock:
    """One content item within a message (text, image, tool result…)."""

    type: str  # "text" | "tool_result" | "image_url"
    text: str | None = None
    tool_call_id: str | None = None  # for tool_result
    content: str | None = None  # body of a tool_result
    image_url: dict[str, Any] | None = None  # {"url": "data:image/…"}


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str | list[ContentBlock]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None  # only for role="tool"
    name: str | None = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class LLMBackend(Protocol):
    """Protocol for LLM backends.

    The caller is responsible for accumulating ``ToolCallDelta`` fragments
    and dispatching the ``ToolCallComplete`` event when ready.
    """

    async def stream(
        self,
        model: str,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMEvent]: ...


# ---------------------------------------------------------------------------
# litellm backend
# ---------------------------------------------------------------------------


def _to_litellm_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg.content, str):
            d: dict[str, Any] = {"role": msg.role, "content": msg.content}
        else:
            # role="tool" with image content needs special handling so that
            # litellm can translate it correctly for both Anthropic (which
            # embeds tool results inside a user message with type="tool_result")
            # and OpenAI-compatible providers.
            if msg.role == "tool" and msg.tool_call_id:
                # Build the inner content list that litellm accepts for a
                # multimodal tool result (image + caption text).
                inner: list[dict[str, Any]] = []
                for block in msg.content:
                    match block.type:
                        case "image_url":
                            inner.append(
                                {"type": "image_url", "image_url": block.image_url}
                            )
                        case "text":
                            inner.append({"type": "text", "text": block.text or ""})
                        case _:
                            pass
                d = {
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": inner,
                }
                out.append(d)
                continue

            parts: list[dict[str, Any]] = []
            for block in msg.content:
                match block.type:
                    case "text":
                        parts.append({"type": "text", "text": block.text or ""})
                    case "tool_result":
                        parts.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.tool_call_id,
                                "content": block.content or "",
                            }
                        )
                    case "image_url":
                        parts.append(
                            {"type": "image_url", "image_url": block.image_url}
                        )
            d = {"role": msg.role, "content": parts}

        if msg.tool_calls:
            d["tool_calls"] = msg.tool_calls
        if msg.tool_call_id:
            d["tool_call_id"] = msg.tool_call_id
        if msg.name:
            d["name"] = msg.name
        out.append(d)
    return out


def _to_litellm_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


class LiteLLMBackend:
    """LLM backend powered by ``litellm``.

    Supports Anthropic, OpenAI, Google Gemini, AWS Bedrock, Azure, Mistral,
    and every other provider that litellm wraps.

    Provider credentials are read from environment variables following litellm
    conventions (e.g. ``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``).
    Pass provider-specific options (``api_base``, ``extra_headers``, etc.)
    as ``**kwargs`` to ``stream()``.
    """

    async def stream(
        self,
        model: str,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMEvent]:
        import litellm  # lazy import — keeps startup fast

        ll_messages = _to_litellm_messages(messages)
        if system:
            ll_messages = [{"role": "system", "content": system}] + ll_messages

        call_kwargs: dict[str, Any] = {"stream": True, **kwargs}
        if tools:
            call_kwargs["tools"] = _to_litellm_tools(tools)
        if temperature is not None:
            call_kwargs["temperature"] = temperature
        if top_p is not None:
            call_kwargs["top_p"] = top_p
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens

        # Accumulate streaming tool calls by index
        pending: dict[int, dict[str, Any]] = {}

        response = await litellm.acompletion(
            model=model, messages=ll_messages, **call_kwargs
        )

        async for chunk in response:
            choice = chunk.choices[0]
            delta = choice.delta

            if delta.content:
                yield TextDelta(content=delta.content)

            # Extended thinking (Anthropic) / o1-style reasoning
            if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                yield ReasoningDelta(content=delta.reasoning_content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in pending:
                        pending[idx] = {"id": tc.id or "", "name": "", "args": ""}
                        yield ToolCallBegin(index=idx, id=tc.id or "", name="")
                    if tc.function:
                        if tc.function.name:
                            pending[idx]["name"] = tc.function.name
                        if tc.function.arguments:
                            pending[idx]["args"] += tc.function.arguments
                            yield ToolCallDelta(
                                index=idx, args_delta=tc.function.arguments
                            )
                    if tc.id and tc.id != pending[idx]["id"]:
                        pending[idx]["id"] = tc.id

            if choice.finish_reason:
                for idx, state in pending.items():
                    yield ToolCallComplete(
                        index=idx,
                        id=state["id"],
                        name=state["name"],
                        arguments=state["args"],
                    )
                raw_usage = getattr(chunk, "usage", None)
                usage: dict[str, Any] = (
                    vars(raw_usage)
                    if raw_usage and hasattr(raw_usage, "__dict__")
                    else {}
                )
                yield StreamDone(
                    stop_reason=choice.finish_reason,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                    cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
                )
                return

        yield StreamDone()


def create_default_backend() -> LiteLLMBackend:
    """Return the default LiteLLM-backed LLM backend."""
    return LiteLLMBackend()


def resolve_model() -> str:
    """Return the litellm model string from the active openvibe config."""

    config = load_config()
    if config.model:
        return f"{config.model.provider_id}/{config.model.model_id}"
    return "azure/gpt-4.1"


def count_tokens(model: str, text: str) -> int:
    """Return the approximate token count for *text* under *model*."""
    return litellm.token_counter(
        model=model,
        messages=[{"role": "system", "content": text}],
    )


def model_context_limits(model: str) -> tuple[int, int]:
    """Return (max_input_tokens, max_output_tokens) for *model*."""
    info = litellm.get_model_info(model)
    return int(info["max_input_tokens"]), int(info["max_output_tokens"])
