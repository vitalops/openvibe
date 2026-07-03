"""Tests for the @tool decorator."""

from __future__ import annotations

import asyncio
import pytest

from openvibe import tool
from openvibe.tool.base import Tool, ToolContext, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx() -> ToolContext:
    return ToolContext(
        session_id="s1",
        message_id="m1",
        agent_name="build",
        project_id="p1",
        working_dir="/tmp",
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Basic decoration
# ---------------------------------------------------------------------------


def test_tool_decorator_returns_tool_instance():
    @tool
    def greet(name: str) -> str:
        """Say hello."""
        return f"Hello, {name}!"

    assert isinstance(greet, Tool)


def test_tool_name_matches_function():
    @tool
    def my_custom_tool(x: str) -> str:
        """Does something."""
        return x

    assert my_custom_tool.name == "my_custom_tool"


def test_tool_description_from_docstring():
    @tool
    def search_jira(query: str, project: str = "ENG") -> str:
        """Search Jira tickets matching a query."""
        return ""

    assert search_jira.description == "Search Jira tickets matching a query."


def test_tool_description_fallback_to_name():
    @tool
    def no_doc(x: str) -> str:
        return x

    assert no_doc.description == "no_doc"


# ---------------------------------------------------------------------------
# Schema generation
# ---------------------------------------------------------------------------


def test_tool_schema_has_required_params():
    @tool
    def echo(message: str) -> str:
        """Echo a message."""
        return message

    schema = echo.parameters_schema()
    assert "message" in schema["properties"]
    assert "message" in schema.get("required", [])


def test_tool_schema_has_optional_param_with_default():
    @tool
    def search(query: str, limit: int = 10) -> str:
        """Search something."""
        return query

    schema = search.parameters_schema()
    assert "limit" in schema["properties"]
    # limit has a default so it must NOT be in required
    assert "limit" not in schema.get("required", [])


def test_tool_schema_type_hints():
    @tool
    def add(a: int, b: int) -> str:
        """Add two numbers."""
        return str(a + b)

    schema = add.parameters_schema()
    assert schema["properties"]["a"]["type"] == "integer"
    assert schema["properties"]["b"]["type"] == "integer"


# ---------------------------------------------------------------------------
# Execution — sync function
# ---------------------------------------------------------------------------


def test_tool_execute_sync():
    @tool
    def double(value: str) -> str:
        """Double a string."""
        return value * 2

    result = _run(double(_ctx(), {"value": "ab"}))
    assert isinstance(result, ToolResult)
    assert result.output == "abab"
    assert result.error is False


def test_tool_execute_sync_with_default():
    @tool
    def greet(name: str, greeting: str = "Hello") -> str:
        """Greet someone."""
        return f"{greeting}, {name}!"

    result = _run(greet(_ctx(), {"name": "world"}))
    assert result.output == "Hello, world!"


def test_tool_execute_bad_params_returns_error():
    @tool
    def typed(count: int) -> str:
        """Needs an int."""
        return str(count)

    result = _run(typed(_ctx(), {"count": "not-an-int"}))
    assert result.error is True


# ---------------------------------------------------------------------------
# Execution — async function
# ---------------------------------------------------------------------------


def test_tool_execute_async():
    @tool
    async def async_echo(msg: str) -> str:
        """Async echo."""
        await asyncio.sleep(0)
        return msg

    result = _run(async_echo(_ctx(), {"msg": "hello"}))
    assert result.output == "hello"
    assert result.error is False


# ---------------------------------------------------------------------------
# Integration with OpenVibe tools list
# ---------------------------------------------------------------------------


def test_tool_registered_in_openvibe(tmp_path):
    from openvibe import OpenVibe
    from openvibe.config import Config

    @tool
    def ping(message: str) -> str:
        """Ping tool."""
        return f"pong: {message}"

    with OpenVibe(project_dir=tmp_path, config=Config(), tools=[ping]) as ov:
        assert ov._registry.get("ping") is ping


def test_register_tool_mid_session(tmp_path):
    from openvibe import OpenVibe
    from openvibe.config import Config

    @tool
    def late_tool(x: str) -> str:
        """Added after start."""
        return x

    with OpenVibe(project_dir=tmp_path, config=Config()) as ov:
        assert ov._registry.get("late_tool") is None
        ov.register_tool(late_tool)
        assert ov._registry.get("late_tool") is late_tool


def test_register_tool_before_start_raises(tmp_path):
    from openvibe import OpenVibe
    from openvibe.config import Config

    @tool
    def early(x: str) -> str:
        """Too early."""
        return x

    ov = OpenVibe(project_dir=tmp_path, config=Config())
    with pytest.raises(RuntimeError):
        ov.register_tool(early)
