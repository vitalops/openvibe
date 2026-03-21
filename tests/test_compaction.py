"""Tests for openvibe.session.compaction — _format_for_summary, compact()."""

from __future__ import annotations

import asyncio

import pytest

from openvibe.config import MessageRole
from openvibe.db import create_database
from openvibe.project import project as _project_module
from openvibe.session import session as _store
from openvibe.session.compaction import _format_for_summary, compact
from openvibe.session.models import CompactionPart, MessageInfo, TextPart


# ---------------------------------------------------------------------------
# _format_for_summary
# ---------------------------------------------------------------------------

def _make_msg(role: MessageRole, text: str) -> MessageInfo:
    """Build a minimal in-memory MessageInfo with a single TextPart."""
    return MessageInfo(
        id=f"msg_{role}",
        session_id="ses_test",
        role=role,
        position=1,
        created_at="2024-01-01T00:00:00+00:00",
        parts=[TextPart(content=text)],
    )


def test_format_for_summary_empty():
    assert _format_for_summary([]) == ""


def test_format_for_summary_single_message():
    msg = _make_msg(MessageRole.USER, "hello")
    result = _format_for_summary([msg])
    assert "USER" in result
    assert "hello" in result


def test_format_for_summary_multiple_messages():
    msgs = [
        _make_msg(MessageRole.USER, "question"),
        _make_msg(MessageRole.ASSISTANT, "answer"),
    ]
    result = _format_for_summary(msgs)
    assert "USER: question" in result
    assert "ASSISTANT: answer" in result


def test_format_for_summary_skips_non_text_parts():
    msg = MessageInfo(
        id="msg_x",
        session_id="ses_test",
        role=MessageRole.ASSISTANT,
        position=1,
        created_at="2024-01-01T00:00:00+00:00",
        parts=[CompactionPart(summary="old summary", message_count=5)],
    )
    result = _format_for_summary([msg])
    # CompactionPart has no .content so nothing is appended
    assert result == ""


# ---------------------------------------------------------------------------
# compact() — early-return when already short
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    db = create_database(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture()
def session(db, tmp_path):
    project = _project_module.get_or_create(db, tmp_path)
    return _store.create(db, project_id=project.id, directory=str(tmp_path))


def test_compact_returns_unchanged_when_short(db, session):
    """compact() must return the original list when len(messages) <= keep_last."""
    msgs = [
        _store.add_message(db, session.id, MessageRole.USER, [TextPart(content=f"msg{i}")])
        for i in range(5)
    ]

    class _NeverCalledLLM:
        async def stream(self, *a, **kw):
            raise AssertionError("LLM should not be called for short histories")
            yield  # make it a generator

    result = asyncio.run(
        compact(db, _NeverCalledLLM(), session.id, "model", msgs, keep_last=10)
    )
    assert result is msgs  # same object — no compaction


def test_compact_calls_llm_and_inserts_compaction_part(db, session):
    """compact() with more messages than keep_last must summarise the oldest ones."""
    msgs = [
        _store.add_message(db, session.id, MessageRole.USER, [TextPart(content=f"msg{i}")])
        for i in range(15)
    ]

    # Minimal async LLM stub that returns a fixed summary
    from openvibe.llm import Message, StreamDone, TextDelta

    class _StubLLM:
        async def stream(self, model, messages, system=None, max_tokens=None):
            yield TextDelta(content="summary text")
            yield StreamDone()

    result = asyncio.run(
        compact(db, _StubLLM(), session.id, "model", msgs, keep_last=10)
    )

    # Should be 1 compaction message + 10 recent messages
    assert len(result) == 11
    assert isinstance(result[0].parts[0], CompactionPart)
    assert result[0].parts[0].summary == "summary text"
    assert result[0].parts[0].message_count == 5  # 15 - 10

    # The compaction message must be persisted to the DB
    db_msgs = _store.list_messages(db, session.id)
    compaction_msgs = [
        m for m in db_msgs
        if any(isinstance(p, CompactionPart) for p in m.parts)
    ]
    assert len(compaction_msgs) == 1
    assert compaction_msgs[0].parts[0].summary == "summary text"


def test_compact_keeps_the_last_n_messages(db, session):
    """The kept messages must be the most recent ones, in original order."""
    msgs = [
        _store.add_message(db, session.id, MessageRole.USER, [TextPart(content=f"msg{i}")])
        for i in range(6)
    ]

    from openvibe.llm import StreamDone, TextDelta

    class _StubLLM:
        async def stream(self, model, messages, system=None, max_tokens=None):
            yield TextDelta(content="sum")
            yield StreamDone()

    result = asyncio.run(
        compact(db, _StubLLM(), session.id, "model", msgs, keep_last=4)
    )

    # compaction + 4 kept messages
    assert len(result) == 5
    # The kept messages are the last 4: msg2, msg3, msg4, msg5
    kept_contents = [p.content for m in result[1:] for p in m.parts if isinstance(p, TextPart)]
    assert kept_contents == ["msg2", "msg3", "msg4", "msg5"]


def test_format_for_summary_multiple_parts_per_message():
    """Messages with multiple TextParts should have each part included."""
    from openvibe.config import MessageRole
    msg = MessageInfo(
        id="msg_multi",
        session_id="ses_test",
        role=MessageRole.USER,
        position=1,
        created_at="2024-01-01T00:00:00+00:00",
        parts=[TextPart(content="part one"), TextPart(content="part two")],
    )
    result = _format_for_summary([msg])
    assert "part one" in result
    assert "part two" in result


def test_format_for_summary_preserves_order():
    """Messages must appear in the order they are given."""
    from openvibe.config import MessageRole
    msgs = [
        MessageInfo(
            id=f"msg_{i}",
            session_id="ses_test",
            role=MessageRole.USER,
            position=i,
            created_at="2024-01-01T00:00:00+00:00",
            parts=[TextPart(content=f"line{i}")],
        )
        for i in range(3)
    ]
    result = _format_for_summary(msgs)
    positions = [result.index(f"line{i}") for i in range(3)]
    assert positions == sorted(positions)
