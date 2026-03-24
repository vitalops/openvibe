"""Tests for openvibe.commands — slash command handlers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from openvibe.commands import CommandContext, execute
from openvibe.config import Config, ModelRef


# ---------------------------------------------------------------------------
# Minimal fakes so we can construct a CommandContext without a real Session
# ---------------------------------------------------------------------------


@dataclass
class _FakeInfo:
    directory: str
    id: str = "fake-session-id"
    project_id: str = "fake-project"
    title: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float = 0.0


class _FakeSession:
    def __init__(self, tmp_path: Path, config: Config | None = None) -> None:
        self._config = config or Config()
        self._agent_name = "build"
        self._permissions = None
        self.info = _FakeInfo(directory=str(tmp_path))
        self._session_config_updates: list[dict] = []

    def update_session_config(self, overrides: dict) -> None:
        """Record the config update (no DB in test fakes)."""
        import copy
        from openvibe.config import _deep_merge
        self._session_config_updates.append(overrides)
        base = self._config.model_dump()
        merged = _deep_merge(base, overrides)
        self._config = Config.model_validate(merged)


def _ctx(tmp_path: Path, args: str = "", config: Config | None = None) -> CommandContext:
    return CommandContext(session=_FakeSession(tmp_path, config), args=args)


# ---------------------------------------------------------------------------
# /model — display
# ---------------------------------------------------------------------------


def test_model_show_current(tmp_path):
    model = ModelRef(provider_id="anthropic", model_id="claude-3")
    result = execute("model", _ctx(tmp_path, config=Config(model=model)))
    assert "anthropic/claude-3" in result.output


def test_model_show_default_when_none(tmp_path):
    result = execute("model", _ctx(tmp_path))
    assert "default" in result.output.lower()


# ---------------------------------------------------------------------------
# /model — session scope (default)
# ---------------------------------------------------------------------------


def test_model_session_scope_default(tmp_path):
    ctx = _ctx(tmp_path, args="openai/gpt-4o")
    result = execute("model", ctx)
    assert "gpt-4o" in result.output
    assert "session" in result.output.lower()
    # In-memory config is updated
    assert ctx.session._config.model.model_id == "gpt-4o"


def test_model_session_scope_explicit(tmp_path):
    ctx = _ctx(tmp_path, args="openai/gpt-4o --session")
    result = execute("model", ctx)
    assert "gpt-4o" in result.output
    assert "session" in result.output.lower()


def test_model_session_scope_no_file_written(tmp_path):
    """Session scope must not write any config files."""
    ctx = _ctx(tmp_path, args="openai/gpt-4o")
    execute("model", ctx)
    assert not (tmp_path / "openvibe.json").exists()


def test_model_infers_provider(tmp_path):
    """When no slash in model arg, infer provider from current model."""
    ctx = _ctx(tmp_path, args="gpt-4o", config=Config(model=ModelRef(provider_id="openai", model_id="gpt-3.5")))
    execute("model", ctx)
    assert ctx.session._config.model.provider_id == "openai"
    assert ctx.session._config.model.model_id == "gpt-4o"


# ---------------------------------------------------------------------------
# /model — project scope
# ---------------------------------------------------------------------------


def test_model_project_scope(tmp_path):
    ctx = _ctx(tmp_path, args="azure/gpt-4.1 --project")
    result = execute("model", ctx)
    assert "azure/gpt-4.1" in result.output
    assert str(tmp_path) in result.output  # shows saved path
    # File should exist with correct content
    cfg = json.loads((tmp_path / "openvibe.json").read_text())
    assert cfg["model"]["provider_id"] == "azure"
    assert cfg["model"]["model_id"] == "gpt-4.1"


def test_model_project_scope_preserves_existing(tmp_path):
    """Writing model to project config must not clobber other keys."""
    (tmp_path / "openvibe.json").write_text(
        json.dumps({"default_agent": "plan", "instructions": ["be brief"]})
    )
    ctx = _ctx(tmp_path, args="openai/gpt-4o --project")
    execute("model", ctx)
    cfg = json.loads((tmp_path / "openvibe.json").read_text())
    assert cfg["model"]["model_id"] == "gpt-4o"
    assert cfg["default_agent"] == "plan"
    assert cfg["instructions"] == ["be brief"]


def test_model_project_scope_targets_dotopenvibe(tmp_path):
    """If .openvibe/openvibe.json exists, write there instead of root."""
    sub = tmp_path / ".openvibe"
    sub.mkdir()
    existing = sub / "openvibe.json"
    existing.write_text(json.dumps({"default_agent": "plan"}))

    ctx = _ctx(tmp_path, args="openai/gpt-4o --project")
    result = execute("model", ctx)
    assert ".openvibe" in result.output
    cfg = json.loads(existing.read_text())
    assert cfg["model"]["model_id"] == "gpt-4o"
    # Root file should NOT have been created
    assert not (tmp_path / "openvibe.json").exists()


def test_model_project_scope_updates_in_memory(tmp_path):
    """--project must also apply the change to the current session."""
    ctx = _ctx(tmp_path, args="azure/gpt-4.1 --project")
    execute("model", ctx)
    assert ctx.session._config.model.model_id == "gpt-4.1"


# ---------------------------------------------------------------------------
# /model — global scope
# ---------------------------------------------------------------------------


def test_model_global_scope(tmp_path, monkeypatch):
    monkeypatch.setattr("openvibe.config.GLOBAL_CONFIG_PATH", tmp_path / "global.json")
    ctx = _ctx(tmp_path, args="anthropic/claude-4 --global")
    result = execute("model", ctx)
    assert "claude-4" in result.output
    cfg = json.loads((tmp_path / "global.json").read_text())
    assert cfg["model"]["provider_id"] == "anthropic"
    assert cfg["model"]["model_id"] == "claude-4"


def test_model_global_scope_preserves_existing(tmp_path, monkeypatch):
    global_path = tmp_path / "global.json"
    global_path.write_text(json.dumps({"default_agent": "plan"}))
    monkeypatch.setattr("openvibe.config.GLOBAL_CONFIG_PATH", global_path)

    ctx = _ctx(tmp_path, args="openai/gpt-4o --global")
    execute("model", ctx)
    cfg = json.loads(global_path.read_text())
    assert cfg["model"]["model_id"] == "gpt-4o"
    assert cfg["default_agent"] == "plan"


def test_model_global_scope_creates_parent_dirs(tmp_path, monkeypatch):
    global_path = tmp_path / "deep" / "nested" / "global.json"
    monkeypatch.setattr("openvibe.config.GLOBAL_CONFIG_PATH", global_path)

    ctx = _ctx(tmp_path, args="openai/gpt-4o --global")
    execute("model", ctx)
    assert global_path.exists()


def test_model_global_scope_updates_in_memory(tmp_path, monkeypatch):
    monkeypatch.setattr("openvibe.config.GLOBAL_CONFIG_PATH", tmp_path / "global.json")
    ctx = _ctx(tmp_path, args="azure/gpt-4.1 --global")
    execute("model", ctx)
    assert ctx.session._config.model.model_id == "gpt-4.1"


# ---------------------------------------------------------------------------
# /model — edge cases
# ---------------------------------------------------------------------------


def test_model_missing_arg_with_scope(tmp_path):
    """Scope flag without a model string should error."""
    result = execute("model", _ctx(tmp_path, args="--global"))
    assert "missing" in result.output.lower() or "red" in result.output.lower()


def test_model_session_scope_records_update(tmp_path):
    """Session scope must call update_session_config with model override."""
    ctx = _ctx(tmp_path, args="azure/gpt-4.1")
    execute("model", ctx)
    assert len(ctx.session._session_config_updates) == 1
    update = ctx.session._session_config_updates[0]
    assert update["model"]["provider_id"] == "azure"
    assert update["model"]["model_id"] == "gpt-4.1"


# ---------------------------------------------------------------------------
# /model — DB round-trip (session config persists across get_session calls)
# ---------------------------------------------------------------------------


def _ov(tmp_path, config=None):
    """Create an OpenVibe instance backed by a real temp DB."""
    from openvibe.config import Config
    from openvibe.db import create_database
    from openvibe.api import OpenVibe

    db = create_database(tmp_path / "test.db")
    return OpenVibe(project_dir=tmp_path, db=db, config=config or Config())


def test_session_config_persists_across_load(tmp_path):
    """update_session_config writes to DB; get_session reads it back."""
    with _ov(tmp_path) as ov:
        session = ov.create_session()
        session.update_session_config(
            {"model": {"provider_id": "azure", "model_id": "gpt-4.1"}}
        )
        # Re-load the session from DB (simulates app restart)
        reloaded = ov.get_session(session.id)
        assert reloaded._config.model is not None
        assert reloaded._config.model.provider_id == "azure"
        assert reloaded._config.model.model_id == "gpt-4.1"


def test_session_config_does_not_leak_to_other_sessions(tmp_path):
    """Config overrides are per-session; a second session sees the base config."""
    with _ov(tmp_path) as ov:
        s1 = ov.create_session()
        s1.update_session_config(
            {"model": {"provider_id": "azure", "model_id": "gpt-4.1"}}
        )
        s2 = ov.create_session()
        # s2 should have no model (base Config has model=None)
        assert s2._config.model is None


def test_session_config_merges_incrementally(tmp_path):
    """Multiple update_session_config calls deep-merge, not overwrite."""
    with _ov(tmp_path) as ov:
        session = ov.create_session()
        session.update_session_config(
            {"model": {"provider_id": "azure", "model_id": "gpt-4.1"}}
        )
        session.update_session_config({"default_agent": "plan"})

        reloaded = ov.get_session(session.id)
        assert reloaded._config.model.model_id == "gpt-4.1"
        assert reloaded._config.default_agent == "plan"
