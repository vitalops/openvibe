"""Provider and model registry.

Abstracts over the many LLM providers supported by litellm.  The registry
maps human-readable provider names to their litellm prefixes and provides
model listing (either from a hardcoded catalogue or by calling the provider's
API).

Adding a new provider
---------------------
Add an entry to ``PROVIDERS`` with the litellm prefix and a list of well-known
models.  The ``list_models()`` function will include them automatically.

API keys
--------
Keys are read from environment variables following litellm conventions.
Per-provider overrides in ``openvibe.json`` (``provider.<id>.api_key``) are
forwarded to litellm as ``api_key`` kwargs on each call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openvibe.config import Config


@dataclass
class ModelInfo:
    id: str              # litellm model string, e.g. "anthropic/claude-sonnet-4-5"
    name: str            # human-readable
    provider_id: str
    context_window: int = 200_000
    supports_vision: bool = False
    supports_tools: bool = True
    supports_reasoning: bool = False


@dataclass
class ProviderInfo:
    id: str              # e.g. "anthropic"
    name: str            # e.g. "Anthropic"
    litellm_prefix: str  # e.g. "anthropic"
    env_key: str | None  # e.g. "ANTHROPIC_API_KEY"
    models: list[ModelInfo] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Provider catalogue
# ---------------------------------------------------------------------------

def _m(provider_id: str, model_id: str, name: str, **kwargs: object) -> ModelInfo:
    return ModelInfo(
        id=f"{provider_id}/{model_id}",
        name=name,
        provider_id=provider_id,
        **kwargs,  # type: ignore[arg-type]
    )


PROVIDERS: list[ProviderInfo] = [
    ProviderInfo(
        id="anthropic",
        name="Anthropic",
        litellm_prefix="anthropic",
        env_key="ANTHROPIC_API_KEY",
        models=[
            _m("anthropic", "claude-opus-4-5", "Claude Opus 4.5", supports_reasoning=True),
            _m("anthropic", "claude-sonnet-4-5", "Claude Sonnet 4.5", context_window=200_000, supports_vision=True),
            _m("anthropic", "claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
        ],
    ),
    ProviderInfo(
        id="openai",
        name="OpenAI",
        litellm_prefix="openai",
        env_key="OPENAI_API_KEY",
        models=[
            _m("openai", "gpt-4o", "GPT-4o", context_window=128_000, supports_vision=True),
            _m("openai", "gpt-4o-mini", "GPT-4o Mini", context_window=128_000),
            _m("openai", "o1", "o1", context_window=200_000, supports_reasoning=True),
            _m("openai", "o1-mini", "o1 Mini", supports_reasoning=True),
            _m("openai", "o3-mini", "o3 Mini", supports_reasoning=True),
        ],
    ),
    ProviderInfo(
        id="google",
        name="Google",
        litellm_prefix="gemini",
        env_key="GEMINI_API_KEY",
        models=[
            _m("gemini", "gemini-2.0-flash", "Gemini 2.0 Flash", context_window=1_000_000, supports_vision=True),
            _m("gemini", "gemini-1.5-pro", "Gemini 1.5 Pro", context_window=2_000_000, supports_vision=True),
            _m("gemini", "gemini-1.5-flash", "Gemini 1.5 Flash", context_window=1_000_000),
        ],
    ),
    ProviderInfo(
        id="groq",
        name="Groq",
        litellm_prefix="groq",
        env_key="GROQ_API_KEY",
        models=[
            _m("groq", "llama-3.3-70b-versatile", "Llama 3.3 70B", context_window=128_000),
            _m("groq", "llama-3.1-8b-instant", "Llama 3.1 8B Instant", context_window=128_000),
            _m("groq", "mixtral-8x7b-32768", "Mixtral 8x7B", context_window=32_768),
        ],
    ),
    ProviderInfo(
        id="mistral",
        name="Mistral",
        litellm_prefix="mistral",
        env_key="MISTRAL_API_KEY",
        models=[
            _m("mistral", "mistral-large-latest", "Mistral Large", context_window=128_000),
            _m("mistral", "mistral-small-latest", "Mistral Small", context_window=32_000),
            _m("mistral", "codestral-latest", "Codestral", context_window=32_000),
        ],
    ),
    ProviderInfo(
        id="openrouter",
        name="OpenRouter",
        litellm_prefix="openrouter",
        env_key="OPENROUTER_API_KEY",
        models=[],  # populated dynamically via list_models_from_api()
    ),
]

_PROVIDER_MAP: dict[str, ProviderInfo] = {p.id: p for p in PROVIDERS}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_provider(provider_id: str) -> ProviderInfo | None:
    return _PROVIDER_MAP.get(provider_id)


def list_providers() -> list[ProviderInfo]:
    return list(PROVIDERS)


def list_models(config: "Config | None" = None) -> list[ModelInfo]:
    """Return all known models across all providers."""
    models = []
    for provider in PROVIDERS:
        models.extend(provider.models)
    return models


def get_model(model_string: str) -> ModelInfo | None:
    """Look up a model by its full litellm string (e.g. 'anthropic/claude-sonnet-4-5')."""
    for model in list_models():
        if model.id == model_string:
            return model
    return None


async def list_models_from_api(provider_id: str, api_key: str | None = None) -> list[ModelInfo]:
    """Fetch the model list from a provider's API (best-effort).

    Falls back to the static catalogue if the API call fails.
    """
    try:
        import litellm
        kwargs: dict[str, object] = {}
        if api_key:
            kwargs["api_key"] = api_key
        provider = get_provider(provider_id)
        if not provider:
            return []
        response = await litellm.amodel_list(provider.litellm_prefix, **kwargs)
        return [
            ModelInfo(
                id=f"{provider.litellm_prefix}/{m.id}",
                name=m.id,
                provider_id=provider_id,
            )
            for m in (response or [])
        ]
    except Exception:
        provider = get_provider(provider_id)
        return provider.models if provider else []
