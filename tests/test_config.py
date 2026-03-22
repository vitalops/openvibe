"""Tests for openvibe.config — deep_merge, expand_env, load_config."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openvibe.config import (
    Config,
    ModelRef,
    _deep_merge,
    _expand_env,
    load_config,
)

# ---------------------------------------------------------------------------
# _deep_merge
# ---------------------------------------------------------------------------


def test_deep_merge_scalars_overlay_wins():
    result = _deep_merge({"a": 1}, {"a": 2})
    assert result["a"] == 2


def test_deep_merge_new_key_added():
    result = _deep_merge({"a": 1}, {"b": 2})
    assert result["a"] == 1
    assert result["b"] == 2


def test_deep_merge_dicts_are_recursive():
    base = {"x": {"a": 1, "b": 2}}
    overlay = {"x": {"b": 99, "c": 3}}
    result = _deep_merge(base, overlay)
    assert result["x"] == {"a": 1, "b": 99, "c": 3}


def test_deep_merge_lists_are_concatenated():
    base = {"instructions": ["first"]}
    overlay = {"instructions": ["second"]}
    result = _deep_merge(base, overlay)
    assert result["instructions"] == ["first", "second"]


def test_deep_merge_base_unmodified():
    base = {"a": {"x": 1}}
    _deep_merge(base, {"a": {"x": 2}})
    assert base["a"]["x"] == 1  # original not mutated


def test_deep_merge_empty_overlay():
    base = {"a": 1}
    assert _deep_merge(base, {}) == {"a": 1}


def test_deep_merge_empty_base():
    assert _deep_merge({}, {"a": 1}) == {"a": 1}


def test_deep_merge_type_mismatch_overlay_wins():
    """If base has a dict and overlay has a scalar for the same key, overlay wins."""
    result = _deep_merge({"a": {"nested": 1}}, {"a": "scalar"})
    assert result["a"] == "scalar"


def test_deep_merge_deeply_nested():
    """Three levels of dict nesting must all merge correctly."""
    base = {"a": {"b": {"c": 1, "d": 2}}}
    overlay = {"a": {"b": {"d": 99, "e": 3}}}
    result = _deep_merge(base, overlay)
    assert result["a"]["b"] == {"c": 1, "d": 99, "e": 3}


# ---------------------------------------------------------------------------
# _expand_env
# ---------------------------------------------------------------------------


def test_expand_env_replaces_known_var(monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret")
    assert _expand_env("${MY_KEY}") == "secret"


def test_expand_env_leaves_unknown_var_unchanged(monkeypatch):
    monkeypatch.delenv("NO_SUCH_VAR", raising=False)
    assert _expand_env("${NO_SUCH_VAR}") == "${NO_SUCH_VAR}"


def test_expand_env_recursive_dict(monkeypatch):
    monkeypatch.setenv("TOKEN", "tok123")
    result = _expand_env({"api_key": "${TOKEN}"})
    assert result == {"api_key": "tok123"}


def test_expand_env_recursive_list(monkeypatch):
    monkeypatch.setenv("VAL", "hello")
    result = _expand_env(["${VAL}", "plain"])
    assert result == ["hello", "plain"]


def test_expand_env_non_string_unchanged():
    assert _expand_env(42) == 42
    assert _expand_env(None) is None


# ---------------------------------------------------------------------------
# load_config — project file
# ---------------------------------------------------------------------------


def test_load_config_empty_dir_returns_defaults(tmp_path):
    cfg = load_config(tmp_path)
    assert isinstance(cfg, Config)
    assert cfg.default_agent == "build"


def test_load_config_reads_project_json(tmp_path):
    (tmp_path / "openvibe.json").write_text(
        json.dumps({"default_agent": "plan"}), encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.default_agent == "plan"


def test_load_config_reads_dotopenvibe_subdir(tmp_path):
    sub = tmp_path / ".openvibe"
    sub.mkdir()
    (sub / "openvibe.json").write_text(
        json.dumps({"default_agent": "general"}), encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.default_agent == "general"


def test_load_config_jsonc_strips_comments(tmp_path):
    jsonc = '{\n  // this is a comment\n  "default_agent": "plan"\n}'
    (tmp_path / "openvibe.jsonc").write_text(jsonc, encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.default_agent == "plan"


def test_load_config_model_roundtrip(tmp_path):
    data = {"model": {"provider_id": "openai", "model_id": "gpt-4o"}}
    (tmp_path / "openvibe.json").write_text(json.dumps(data), encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.model == ModelRef(provider_id="openai", model_id="gpt-4o")


def test_load_config_instructions_concatenated(tmp_path):
    data = {"instructions": ["be brief"]}
    (tmp_path / "openvibe.json").write_text(json.dumps(data), encoding="utf-8")
    cfg = load_config(tmp_path)
    # instructions may be merged with any global config; at minimum our entry is present
    assert "be brief" in cfg.instructions


# ---------------------------------------------------------------------------
# load_config — env var overrides
# ---------------------------------------------------------------------------


def test_load_config_env_content_override(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "OPENVIBE_CONFIG_CONTENT",
        json.dumps({"default_agent": "general"}),
    )
    cfg = load_config(tmp_path)
    assert cfg.default_agent == "general"


def test_load_config_env_file_override(tmp_path, monkeypatch):
    cfg_file = tmp_path / "override.json"
    cfg_file.write_text(json.dumps({"default_agent": "plan"}), encoding="utf-8")
    monkeypatch.setenv("OPENVIBE_CONFIG", str(cfg_file))
    cfg = load_config(tmp_path)
    assert cfg.default_agent == "plan"


def test_load_config_env_expands_vars_in_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_PROVIDER", "anthropic")
    data = {"model": {"provider_id": "${MY_PROVIDER}", "model_id": "claude-3"}}
    (tmp_path / "openvibe.json").write_text(json.dumps(data), encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.model.provider_id == "anthropic"


def test_load_config_project_json_takes_priority_over_dotopenvibe(tmp_path):
    """openvibe.json at the root takes precedence over .openvibe/openvibe.json."""
    (tmp_path / "openvibe.json").write_text(
        json.dumps({"default_agent": "build"}), encoding="utf-8"
    )
    sub = tmp_path / ".openvibe"
    sub.mkdir()
    (sub / "openvibe.json").write_text(
        json.dumps({"default_agent": "general"}), encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    # Root openvibe.json wins — only the first matching candidate is loaded
    assert cfg.default_agent == "build"


def test_load_config_no_project_dir():
    """load_config(None) must not raise and return a valid Config."""
    cfg = load_config(None)
    assert isinstance(cfg, Config)
