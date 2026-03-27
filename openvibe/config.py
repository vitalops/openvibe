"""Configuration system.

Config is loaded in priority order (lowest → highest):

1. Built-in defaults (hardcoded)
2. Global user config  — ``~/.config/openvibe/openvibe.json``
3. Project config      — ``./openvibe.json`` or ``./.openvibe/openvibe.json``
4. Environment vars    — ``OPENVIBE_*`` prefixed variables

Later sources deep-merge into earlier ones; arrays (``plugins``,
``instructions``) are concatenated, not replaced.

All schemas are Pydantic models so validation errors surface with clear
messages at startup rather than at runtime.
"""

from __future__ import annotations

import json
import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from jsonc_parser.parser import JsoncParser
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Enums — defined here so every module imports from one place
# ---------------------------------------------------------------------------


class AgentMode(StrEnum):
    PRIMARY = "primary"
    SUBAGENT = "subagent"


class PermissionAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    ERROR = "error"
    PERMISSION = "permission"


class ToolStateStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ModelRef(BaseModel):
    """Reference to a specific model on a specific provider."""

    provider_id: str
    model_id: str


class ProviderConfig(BaseModel):
    """Per-provider overrides (api_key, base_url, api_version, custom options)."""

    api_key: str | None = None
    base_url: str | None = None
    api_version: str | None = None  # e.g. "2024-02-01" for Azure OpenAI
    # Arbitrary provider-specific options forwarded to litellm
    options: dict[str, Any] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    """Definition for a named agent (built-in or user-defined)."""

    model: ModelRef | None = None
    description: str = ""
    prompt: str | None = None  # additional system prompt fragment
    temperature: float | None = None
    top_p: float | None = None
    max_steps: int | None = None  # hard cap on tool-call iterations
    mode: AgentMode = AgentMode.PRIMARY


class PermissionRule(BaseModel):
    """A single permission rule evaluated against a tool call."""

    tool: str  # tool name or glob (e.g. "bash", "file.*")
    action: PermissionAction
    pattern: str | None = None  # optional path / argument pattern


class McpServerConfig(BaseModel):
    """Configuration for an MCP server connection."""

    type: Literal["stdio", "sse"] = "stdio"
    # stdio transport
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # SSE / HTTP transport
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class Config(BaseModel):
    """Root application configuration.

    Typical ``openvibe.json``::

        {
          "model": {"provider_id": "anthropic", "model_id": "claude-sonnet-4-5"},
          "provider": {
            "anthropic": {"api_key": "${ANTHROPIC_API_KEY}"}
          },
          "agent": {
            "build": {"temperature": 0.2}
          },
          "permission": [
            {"tool": "bash", "action": "ask"},
            {"tool": "file.*", "action": "allow"}
          ]
        }
    """

    # Default model used when no agent-specific model is set
    model: ModelRef | None = None
    # Provider overrides keyed by provider_id
    provider: dict[str, ProviderConfig] = Field(default_factory=dict)
    # Agent overrides / custom agents keyed by agent name
    agent: dict[str, AgentConfig] = Field(default_factory=dict)
    # Project-level default permission rules (evaluated before agent rules)
    permission: list[PermissionRule] = Field(default_factory=list)
    # MCP server configs keyed by server name
    mcp: dict[str, McpServerConfig] = Field(default_factory=dict)
    # Extra system-prompt fragments appended to every agent's prompt
    instructions: list[str] = Field(default_factory=list)
    # Name of the agent to use by default (overrides "build")
    default_agent: str = "build"
    # Keybinds and other UI settings are intentionally omitted from the core


# ---------------------------------------------------------------------------
# Environment variable expansion
# ---------------------------------------------------------------------------

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _expand_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` references in string values."""
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Deep merge
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge *overlay* into a copy of *base*.

    - Dicts are merged recursively.
    - Lists are concatenated (so ``instructions`` and ``plugins`` accumulate).
    - All other types: overlay wins.
    """
    result = dict(base)
    for key, val in overlay.items():
        if key in result:
            if isinstance(result[key], dict) and isinstance(val, dict):
                result[key] = _deep_merge(result[key], val)
            elif isinstance(result[key], list) and isinstance(val, list):
                result[key] = result[key] + val
            else:
                result[key] = val
        else:
            result[key] = val
    return result


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return JsoncParser.parse_file(str(path))
    except Exception as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def _try_load(path: Path) -> dict[str, Any]:
    """Return parsed JSON from *path*, or empty dict if the file is absent."""
    if path.exists():
        return _load_json(path)
    return {}


def load_config(project_dir: Path | None = None) -> Config:
    """Load and merge configuration from all sources.

    Priority (lowest → highest):
    1. Built-in defaults
    2. ``~/.config/openvibe/openvibe.json``
    3. ``<project>/openvibe.json`` or ``<project>/.openvibe/openvibe.json``
    4. ``OPENVIBE_CONFIG`` env var (path to a JSON file)
    5. ``OPENVIBE_CONFIG_CONTENT`` env var (raw JSON string)
    """
    raw: dict[str, Any] = {}

    # 2. Global user config
    global_cfg = Path.home() / ".config" / "openvibe" / "openvibe.json"
    raw = _deep_merge(raw, _try_load(global_cfg))

    # 3. Project config
    if project_dir:
        for candidate in [
            project_dir / "openvibe.json",
            project_dir / "openvibe.jsonc",
            project_dir / ".openvibe" / "openvibe.json",
            project_dir / ".openvibe" / "openvibe.jsonc",
        ]:
            if candidate.exists():
                raw = _deep_merge(raw, _load_json(candidate))
                break

    # 4. OPENVIBE_CONFIG env var (path)
    if cfg_path := os.environ.get("OPENVIBE_CONFIG"):
        raw = _deep_merge(raw, _load_json(Path(cfg_path)))

    # 5. OPENVIBE_CONFIG_CONTENT env var (inline JSON)
    if cfg_content := os.environ.get("OPENVIBE_CONFIG_CONTENT"):
        raw = _deep_merge(raw, JsoncParser.parse_str(cfg_content))

    raw = _expand_env(raw)
    config = Config.model_validate(raw)
    _apply_provider_env(config)
    return config


# Env var names used by litellm for each provider field.
# provider_id → (api_key_var, base_url_var, api_version_var)
_PROVIDER_ENV: dict[str, tuple[str | None, str | None, str | None]] = {
    "anthropic": ("ANTHROPIC_API_KEY", None, None),
    "openai": ("OPENAI_API_KEY", "OPENAI_API_BASE", None),
    "google": ("GEMINI_API_KEY", None, None),
    "groq": ("GROQ_API_KEY", None, None),
    "mistral": ("MISTRAL_API_KEY", None, None),
    "openrouter": ("OPENROUTER_API_KEY", None, None),
    "azure": ("AZURE_API_KEY", "AZURE_API_BASE", "AZURE_API_VERSION"),
}


GLOBAL_CONFIG_PATH = Path.home() / ".config" / "openvibe" / "openvibe.json"


def _find_project_config(project_dir: Path) -> Path:
    """Return the existing project config path, or the default location."""
    for candidate in [
        project_dir / "openvibe.json",
        project_dir / "openvibe.jsonc",
        project_dir / ".openvibe" / "openvibe.json",
        project_dir / ".openvibe" / "openvibe.jsonc",
    ]:
        if candidate.exists():
            return candidate
    return project_dir / "openvibe.json"


def _read_modify_write(path: Path, updates: dict[str, Any]) -> None:
    """Read a JSON file, merge *updates* in, and write it back."""
    existing: dict[str, Any] = {}
    if path.exists():
        existing = _load_json(path)
    existing.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")


def save_model_to_global(model: ModelRef) -> Path:
    """Persist *model* to the global user config file."""
    _read_modify_write(
        GLOBAL_CONFIG_PATH,
        {"model": {"provider_id": model.provider_id, "model_id": model.model_id}},
    )
    return GLOBAL_CONFIG_PATH


def save_model_to_project(model: ModelRef, project_dir: Path) -> Path:
    """Persist *model* to the project config file."""
    path = _find_project_config(project_dir)
    _read_modify_write(
        path,
        {"model": {"provider_id": model.provider_id, "model_id": model.model_id}},
    )
    return path


def _apply_provider_env(config: Config) -> None:
    """Push provider config values into os.environ so litellm picks them up.

    Only sets variables that are absent from the environment — existing env
    vars (e.g. already exported in the shell) always take precedence.
    """
    for provider_id, pcfg in config.provider.items():
        key_var, base_var, ver_var = _PROVIDER_ENV.get(provider_id, (None, None, None))
        if pcfg.api_key and key_var:
            os.environ.setdefault(key_var, pcfg.api_key)
        if pcfg.base_url and base_var:
            os.environ.setdefault(base_var, pcfg.base_url)
        if pcfg.api_version and ver_var:
            os.environ.setdefault(ver_var, pcfg.api_version)