from __future__ import annotations

import pytest

from openvibe.tool.base import ToolContext, create_default_registry
from openvibe.tool.web_search import WebSearchTool

pytestmark = pytest.mark.integration


@pytest.fixture()
def ctx(tmp_path):
    return ToolContext(
        session_id="test",
        message_id="test",
        agent_name="test",
        project_id="test",
        working_dir=str(tmp_path),
    )


@pytest.fixture()
def tool():
    return WebSearchTool()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registered_in_default_registry():
    registry = create_default_registry()
    assert registry.get("web_search") is not None


def test_tool_name():
    assert WebSearchTool().name == "web_search"


# ---------------------------------------------------------------------------
# Real searches
# ---------------------------------------------------------------------------


async def test_basic_search_returns_results(ctx, tool):
    result = await tool(ctx, {"query": "Python programming language"})

    assert not result.error
    assert result.metadata["num_results"] > 0
    assert "python" in result.output.lower()


async def test_results_contain_title_url_snippet(ctx, tool):
    result = await tool(ctx, {"query": "OpenAI GPT-4"})

    assert not result.error
    assert result.metadata["num_results"] > 0
    assert "1." in result.output
    assert "URL: http" in result.output


async def test_num_results_respected(ctx, tool):
    result = await tool(ctx, {"query": "machine learning", "num_results": 3})

    assert not result.error
    assert result.metadata["num_results"] == 3
    assert "3." in result.output
    assert "4." not in result.output


async def test_default_num_results_is_eight(ctx, tool):
    result = await tool(ctx, {"query": "artificial intelligence"})
    assert not result.error
    assert result.metadata["num_results"] <= 8


async def test_specific_query_returns_relevant_results(ctx, tool):
    result = await tool(ctx, {"query": "python requests library documentation"})

    assert not result.error
    assert result.metadata["num_results"] > 0
    assert "python" in result.output.lower() or "requests" in result.output.lower()


async def test_pagination_returns_different_results(ctx, tool):
    result_page1 = await tool(ctx, {"query": "openai", "num_results": 3, "start_page": 1})
    result_page2 = await tool(ctx, {"query": "openai", "num_results": 3, "start_page": 2})

    assert not result_page1.error
    assert not result_page2.error
    assert result_page1.metadata["num_results"] > 0
    assert result_page2.metadata["num_results"] > 0
    assert result_page1.output != result_page2.output


async def test_start_page_metadata_present(ctx, tool):
    result = await tool(ctx, {"query": "linux kernel", "start_page": 2, "num_results": 3})

    assert not result.error
    assert result.metadata["start_page"] == 2



async def test_invalid_num_results_rejected(ctx, tool):
    result = await tool(ctx, {"query": "test", "num_results": 99})
    assert result.error