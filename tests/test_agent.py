"""Tests for openvibe.agent.agent — resolve(), list_agents(), _apply_config()."""

from __future__ import annotations

import pytest

from openvibe.agent.agent import AgentInfo, list_agents, resolve
from openvibe.config import AgentConfig, AgentMode, Config, ModelRef

# ---------------------------------------------------------------------------
# resolve() — built-in agents
# ---------------------------------------------------------------------------


def test_resolve_build_is_default():
    cfg = Config()
    agent = resolve(cfg)
    assert agent.name == "build"


def test_resolve_build_has_bash_rule():
    cfg = Config()
    agent = resolve(cfg, "build")
    rule_names = {r.tool for r in agent.permission_rules}
    assert "bash" in rule_names


def test_resolve_plan_denies_bash():
    from openvibe.config import PermissionAction

    cfg = Config()
    agent = resolve(cfg, "plan")
    bash_rules = [r for r in agent.permission_rules if r.tool == "bash"]
    assert bash_rules
    assert bash_rules[0].action == PermissionAction.DENY


def test_resolve_plan_has_disabled_tools():
    cfg = Config()
    agent = resolve(cfg, "plan")
    assert "bash" in agent.disabled_tools
    assert "write" in agent.disabled_tools


def test_resolve_general_is_subagent():
    cfg = Config()
    agent = resolve(cfg, "general")
    assert agent.mode == AgentMode.SUBAGENT


def test_resolve_unknown_agent_returns_shell():
    cfg = Config()
    agent = resolve(cfg, "my_custom_agent")
    assert agent.name == "my_custom_agent"
    assert agent.system_prompt == ""


# ---------------------------------------------------------------------------
# resolve() — user config overrides
# ---------------------------------------------------------------------------


def test_resolve_applies_temperature_override():
    cfg = Config(agent={"build": AgentConfig(temperature=0.1)})
    agent = resolve(cfg, "build")
    assert agent.temperature == 0.1


def test_resolve_applies_max_steps_override():
    cfg = Config(agent={"build": AgentConfig(max_steps=10)})
    agent = resolve(cfg, "build")
    assert agent.max_steps == 10


def test_resolve_applies_model_override():
    model = ModelRef(provider_id="openai", model_id="gpt-4o")
    cfg = Config(agent={"build": AgentConfig(model=model)})
    agent = resolve(cfg, "build")
    assert agent.model == model


def test_resolve_appends_custom_prompt():
    cfg = Config(agent={"build": AgentConfig(prompt="extra instructions")})
    agent = resolve(cfg, "build")
    # Custom prompt is appended — original must still be present
    assert "extra instructions" in agent.system_prompt
    assert "openvibe" in agent.system_prompt.lower()  # original build prompt preserved


def test_resolve_mode_override():
    cfg = Config(agent={"build": AgentConfig(mode=AgentMode.SUBAGENT)})
    agent = resolve(cfg, "build")
    assert agent.mode == AgentMode.SUBAGENT


def test_resolve_global_model_applied_when_agent_has_none():
    global_model = ModelRef(provider_id="anthropic", model_id="claude-3")
    cfg = Config(model=global_model)
    agent = resolve(cfg, "build")
    assert agent.model == global_model


def test_resolve_agent_model_overrides_global():
    global_model = ModelRef(provider_id="anthropic", model_id="claude-3")
    agent_model = ModelRef(provider_id="openai", model_id="gpt-4o")
    cfg = Config(
        model=global_model,
        agent={"build": AgentConfig(model=agent_model)},
    )
    agent = resolve(cfg, "build")
    assert agent.model == agent_model


def test_resolve_global_instructions_appended():
    cfg = Config(instructions=["always be concise"])
    agent = resolve(cfg, "build")
    assert "always be concise" in agent.extra_instructions


def test_resolve_respects_default_agent_config():
    cfg = Config(default_agent="plan")
    agent = resolve(cfg)
    assert agent.name == "plan"


# ---------------------------------------------------------------------------
# list_agents()
# ---------------------------------------------------------------------------


def test_list_agents_includes_builtins():
    cfg = Config()
    names = {a.name for a in list_agents(cfg)}
    assert {"build", "plan", "general"}.issubset(names)


def test_list_agents_includes_custom():
    cfg = Config(agent={"my_agent": AgentConfig(description="custom")})
    names = {a.name for a in list_agents(cfg)}
    assert "my_agent" in names


def test_list_agents_returns_agent_info_instances():
    cfg = Config()
    agents = list_agents(cfg)
    assert all(isinstance(a, AgentInfo) for a in agents)


def test_resolve_applies_top_p_override():
    cfg = Config(agent={"build": AgentConfig(top_p=0.9)})
    agent = resolve(cfg, "build")
    assert agent.top_p == 0.9


def test_resolve_custom_agent_gets_global_instructions():
    """A freshly-created custom agent should also get global instructions."""
    cfg = Config(
        agent={"custom": AgentConfig(description="my agent")},
        instructions=["be polite"],
    )
    agent = resolve(cfg, "custom")
    assert "be polite" in agent.extra_instructions


def test_list_agents_deduplicates_builtin_override():
    """Overriding a built-in by name should produce only one agent in the list."""
    cfg = Config(agent={"build": AgentConfig(temperature=0.5)})
    names = [a.name for a in list_agents(cfg)]
    assert names.count("build") == 1
