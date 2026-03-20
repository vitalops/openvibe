"""Context compaction.

When the accumulated message history grows too large for the model's context
window, the processor emits a ``CompactionNeeded`` event.  This module handles
that by summarising the oldest messages via a cheap summarisation call and
replacing them with a ``CompactionPart``.

This keeps the effective context within limits while preserving the key
facts from earlier in the conversation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from openvibe.session.models import CompactionPart, MessageInfo, TextPart

if TYPE_CHECKING:
    from openvibe.db import Database
    from openvibe.llm import LLMBackend

logger = logging.getLogger(__name__)

_SUMMARISE_PROMPT = """\
You are a summarisation assistant. Below is a partial conversation history.
Produce a concise summary (≤400 words) that preserves:
- The user's original goal
- Key decisions made and code changes applied
- Any outstanding tasks or open questions

Do not include raw code diffs — describe changes in prose.
"""


async def compact(
    db: "Database",
    llm: "LLMBackend",
    session_id: str,
    model: str,
    messages: list[MessageInfo],
    keep_last: int = 10,
) -> list[MessageInfo]:
    """Summarise the *oldest* messages and return the trimmed message list.

    Keeps the most recent *keep_last* messages unchanged so the LLM retains
    immediate context. The older messages are replaced with a single
    CompactionPart on a synthetic assistant message.

    Returns the new list of messages to use for the next LLM call.
    """
    if len(messages) <= keep_last:
        return messages

    to_compact = messages[:-keep_last]
    to_keep = messages[-keep_last:]

    # Build a text dump of the messages to summarise
    history_text = _format_for_summary(to_compact)

    summary = await _call_summary_model(llm, model, history_text)

    # Create a synthetic message with the compaction part
    from openvibe.session import session as session_store
    from openvibe.session.models import now_iso

    compaction_msg = session_store.add_message(
        db,
        session_id,
        "assistant",
        [CompactionPart(summary=summary, message_count=len(to_compact))],
    )

    logger.info(
        "Compacted %d messages into 1 for session %s", len(to_compact), session_id
    )

    return [compaction_msg] + to_keep


async def _call_summary_model(llm: "LLMBackend", model: str, text: str) -> str:
    from openvibe.llm import Message, StreamDone, TextDelta

    messages = [Message(role="user", content=text)]
    chunks: list[str] = []

    async for event in llm.stream(
        model=model,
        messages=messages,
        system=_SUMMARISE_PROMPT,
        max_tokens=500,
    ):
        match event:
            case TextDelta(content=content):
                chunks.append(content)
            case StreamDone():
                break

    return "".join(chunks).strip()


def _format_for_summary(messages: list[MessageInfo]) -> str:
    lines: list[str] = []
    for msg in messages:
        role = msg.role.upper()
        text_parts = [p for p in msg.parts if isinstance(p, TextPart)]
        for part in text_parts:
            lines.append(f"{role}: {part.content}")
    return "\n".join(lines)
