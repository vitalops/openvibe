"""Tests for openvibe.session.session — persistence layer (CRUD, fork, archive)."""

from __future__ import annotations

import pytest

from openvibe.config import MessageRole
from openvibe.db import create_database
from openvibe.project import project as _project_module
from openvibe.session import session as _store
from openvibe.session.models import TextPart

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    db = create_database(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture()
def project(db, tmp_path):
    return _project_module.get_or_create(db, tmp_path)


def _make_session(db, project, title=None):
    return _store.create(
        db,
        project_id=project.id,
        directory=project.path,
        title=title,
    )


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------


def test_create_returns_session_info(db, project):
    info = _make_session(db, project)
    assert info.id.startswith("ses_")
    assert info.project_id == project.id


def test_get_returns_same_session(db, project):
    created = _make_session(db, project)
    fetched = _store.get(db, created.id)
    assert fetched is not None
    assert fetched.id == created.id


def test_get_missing_returns_none(db):
    assert _store.get(db, "ses_doesnotexist") is None


def test_list_sessions_empty(db, project):
    assert _store.list_sessions(db, project.id) == []


def test_list_sessions_returns_created(db, project):
    s1 = _make_session(db, project, title="a")
    s2 = _make_session(db, project, title="b")
    ids = {s.id for s in _store.list_sessions(db, project.id)}
    assert {s1.id, s2.id}.issubset(ids)


def test_delete_removes_session(db, project):
    s = _make_session(db, project)
    _store.delete(db, s.id)
    assert _store.get(db, s.id) is None


# ---------------------------------------------------------------------------
# archive() vs delete()
# ---------------------------------------------------------------------------


def test_archive_hides_from_listing(db, project):
    s = _make_session(db, project)
    _store.archive(db, s.id)
    ids = [x.id for x in _store.list_sessions(db, project.id)]
    assert s.id not in ids


def test_archive_session_still_retrievable_by_id(db, project):
    s = _make_session(db, project)
    _store.archive(db, s.id)
    fetched = _store.get(db, s.id)
    assert fetched is not None
    assert fetched.archived_at is not None


# ---------------------------------------------------------------------------
# update_title / update_cost
# ---------------------------------------------------------------------------


def test_update_title(db, project):
    s = _make_session(db, project, title="old")
    _store.update_title(db, s.id, "new title")
    assert _store.get(db, s.id).title == "new title"


def test_update_cost_accumulates(db, project):
    s = _make_session(db, project)
    _store.update_cost(db, s.id, cost=0.01, input_tokens=100, output_tokens=50)
    _store.update_cost(db, s.id, cost=0.02, input_tokens=200, output_tokens=80)
    updated = _store.get(db, s.id)
    assert abs(updated.cost - 0.03) < 1e-6
    assert updated.input_tokens == 300
    assert updated.output_tokens == 130


# ---------------------------------------------------------------------------
# Message CRUD
# ---------------------------------------------------------------------------


def test_add_and_list_messages(db, project):
    s = _make_session(db, project)
    _store.add_message(db, s.id, MessageRole.USER, [TextPart(content="hello")])
    _store.add_message(db, s.id, MessageRole.ASSISTANT, [TextPart(content="hi")])
    msgs = _store.list_messages(db, s.id)
    assert len(msgs) == 2
    roles = {m.role for m in msgs}
    assert MessageRole.USER in roles
    assert MessageRole.ASSISTANT in roles


def test_messages_ordered_by_position(db, project):
    s = _make_session(db, project)
    for i in range(5):
        _store.add_message(db, s.id, MessageRole.USER, [TextPart(content=str(i))])
    msgs = _store.list_messages(db, s.id)
    positions = [m.position for m in msgs]
    assert positions == sorted(positions)


def test_upsert_part_updates_existing(db, project):
    s = _make_session(db, project)
    msg = _store.add_message(db, s.id, MessageRole.ASSISTANT)
    _store.upsert_part(db, msg.id, 0, TextPart(content="v1"))
    _store.upsert_part(db, msg.id, 0, TextPart(content="v2"))
    msgs = _store.list_messages(db, s.id)
    assert msgs[0].parts[0].content == "v2"


# ---------------------------------------------------------------------------
# fork()
# ---------------------------------------------------------------------------


def test_fork_creates_new_session(db, project):
    original = _make_session(db, project)
    forked = _store.fork(db, original.id)
    assert forked.id != original.id
    assert forked.parent_id == original.id


def test_fork_copies_all_messages(db, project):
    original = _make_session(db, project)
    _store.add_message(db, original.id, MessageRole.USER, [TextPart(content="q")])
    _store.add_message(db, original.id, MessageRole.ASSISTANT, [TextPart(content="a")])

    forked = _store.fork(db, original.id)
    forked_msgs = _store.list_messages(db, forked.id)
    assert len(forked_msgs) == 2


def test_fork_up_to_message_id_truncates(db, project):
    original = _make_session(db, project)
    m1 = _store.add_message(db, original.id, MessageRole.USER, [TextPart(content="q1")])
    _store.add_message(db, original.id, MessageRole.ASSISTANT, [TextPart(content="a1")])
    _store.add_message(db, original.id, MessageRole.USER, [TextPart(content="q2")])

    # Fork up to (not including) m1 — result should have 0 messages
    forked = _store.fork(db, original.id, up_to_message_id=m1.id)
    assert _store.list_messages(db, forked.id) == []


def test_fork_nonexistent_session_raises(db):
    with pytest.raises(ValueError, match="not found"):
        _store.fork(db, "ses_doesnotexist")


def test_fork_middle_truncation(db, project):
    """Forking up to message 2 of 3 copies exactly the first message."""
    original = _make_session(db, project)
    m1 = _store.add_message(db, original.id, MessageRole.USER, [TextPart(content="q1")])
    m2 = _store.add_message(
        db, original.id, MessageRole.ASSISTANT, [TextPart(content="a1")]
    )
    _store.add_message(db, original.id, MessageRole.USER, [TextPart(content="q2")])

    # Fork up to (not including) m2 — should copy only m1
    forked = _store.fork(db, original.id, up_to_message_id=m2.id)
    msgs = _store.list_messages(db, forked.id)
    assert len(msgs) == 1
    assert msgs[0].parts[0].content == "q1"


def test_fork_copies_part_content(db, project):
    """Forked messages must preserve part content, not just count."""
    original = _make_session(db, project)
    _store.add_message(
        db, original.id, MessageRole.USER, [TextPart(content="exact text")]
    )

    forked = _store.fork(db, original.id)
    msgs = _store.list_messages(db, forked.id)
    assert msgs[0].parts[0].content == "exact text"


# ---------------------------------------------------------------------------
# add_message edge cases
# ---------------------------------------------------------------------------


def test_add_message_without_parts(db, project):
    """add_message with no parts should succeed and produce an empty parts list."""
    s = _make_session(db, project)
    msg = _store.add_message(db, s.id, MessageRole.ASSISTANT)
    loaded = _store.list_messages(db, s.id)
    assert loaded[0].id == msg.id
    assert loaded[0].parts == []


# ---------------------------------------------------------------------------
# update_cost cache tokens
# ---------------------------------------------------------------------------


def test_update_cost_cache_tokens_accumulate(db, project):
    """cache_read_tokens and cache_write_tokens must accumulate correctly."""
    s = _make_session(db, project)
    _store.update_cost(
        db,
        s.id,
        cost=0.0,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=10,
        cache_write_tokens=5,
    )
    _store.update_cost(
        db,
        s.id,
        cost=0.0,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=20,
        cache_write_tokens=8,
    )
    updated = _store.get(db, s.id)
    assert updated.cache_read_tokens == 30
    assert updated.cache_write_tokens == 13


# ---------------------------------------------------------------------------
# list_sessions ordering
# ---------------------------------------------------------------------------


def test_list_sessions_ordered_by_updated_at_desc(db, project):
    """Most recently updated session should appear first."""
    import time as _time

    s1 = _make_session(db, project, title="first")
    _time.sleep(0.01)  # ensure different timestamps
    s2 = _make_session(db, project, title="second")

    sessions = _store.list_sessions(db, project.id)
    ids_in_order = [s.id for s in sessions]
    # s2 was created (and thus updated) more recently, so it should come first
    assert ids_in_order.index(s2.id) < ids_in_order.index(s1.id)
