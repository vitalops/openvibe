"""Tests for the OrcaRouter provider integration."""

from __future__ import annotations

from openvibe.config import _PROVIDER_ENV
from openvibe.llm import ORCAROUTER_BASE_URL, normalize_litellm_model
from openvibe.provider.provider import get_provider


def test_orcarouter_provider_is_registered():
    provider = get_provider("orcarouter")
    assert provider is not None
    assert provider.name == "OrcaRouter"
    assert provider.litellm_prefix == "orcarouter"
    assert provider.env_key == "ORCAROUTER_API_KEY"
    # Named starter model list, mirroring the gateway's namespaced IDs.
    assert any(m.id == "orcarouter/openai/gpt-5.5" for m in provider.models)


def test_orcarouter_env_tuple_matches_provider():
    assert _PROVIDER_ENV["orcarouter"] == ("ORCAROUTER_API_KEY", None, None)


def test_normalize_litellm_model_routes_orcarouter_via_openai():
    litellm_model, kwargs = normalize_litellm_model("orcarouter/openai/gpt-5.5")
    assert litellm_model == "openai/openai/gpt-5.5"
    assert kwargs["api_base"] == ORCAROUTER_BASE_URL
    assert "api_key" not in kwargs  # only injected when env var is set


def test_normalize_litellm_model_injects_api_key_when_set(monkeypatch):
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test")
    _, kwargs = normalize_litellm_model("orcarouter/anthropic/claude-opus-4.8")
    assert kwargs["api_key"] == "sk-orca-test"


def test_normalize_litellm_model_leaves_other_providers_unchanged():
    model, kwargs = normalize_litellm_model("openrouter/openai/gpt-4o")
    assert model == "openrouter/openai/gpt-4o"
    assert kwargs == {}
