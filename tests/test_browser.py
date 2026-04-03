from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openvibe.tool.base import ToolContext, create_default_registry
from openvibe.tool.browser import WebBrowserTool

_SAMPLE_HTML = """
<html><body>
  <nav>Site navigation</nav>
  <header>Site header</header>
  <main>
    <p>Welcome to the test page.</p>
    <a href="https://example.com/one">Link One</a>
    <a href="https://example.com/two">Link Two</a>
  </main>
  <footer>Site footer</footer>
  <script>var x = 1;</script>
  <style>.foo { color: red; }</style>
</body></html>
"""


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
    t = WebBrowserTool()
    t._get = MagicMock(return_value=_SAMPLE_HTML)
    mock_summarizer = AsyncMock()
    mock_summarizer.summarize.return_value = ("Page summary.", ["Page summary."])
    t.summarizer = mock_summarizer
    return t


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registered_in_default_registry():
    registry = create_default_registry()
    assert registry.get("web_browser") is not None


def test_tool_name():
    assert WebBrowserTool().name == "web_browser"


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------


async def test_output_contains_summary_and_links(ctx, tool):
    result = await tool(ctx, {"url": "https://example.com", "query": "test"})

    assert not result.error
    assert "Page summary." in result.output
    assert "Links found on the page:" in result.output


async def test_links_in_output(ctx, tool):
    result = await tool(ctx, {"url": "https://example.com"})

    assert "https://example.com/one" in result.output
    assert "https://example.com/two" in result.output


async def test_links_capped_at_five(ctx, tool):
    many_links = "".join(
        f'<a href="https://example.com/{i}">Link {i}</a>' for i in range(10)
    )
    tool._get = MagicMock(return_value=f"<html><body>{many_links}</body></html>")

    result = await tool(ctx, {"url": "https://example.com"})

    link_lines = [l for l in result.output.splitlines() if "https://example.com/" in l]
    assert len(link_lines) <= 5


# ---------------------------------------------------------------------------
# HTML parsing — what reaches the summarizer
# ---------------------------------------------------------------------------


async def test_scripts_stripped_before_summarizer(ctx, tool):
    await tool(ctx, {"url": "https://example.com"})

    text_passed = tool.summarizer.summarize.call_args[0][0]
    assert "var x = 1" not in text_passed
    assert "<script" not in text_passed


async def test_styles_stripped_before_summarizer(ctx, tool):
    await tool(ctx, {"url": "https://example.com"})

    text_passed = tool.summarizer.summarize.call_args[0][0]
    assert ".foo" not in text_passed
    assert "<style" not in text_passed


async def test_nav_stripped_before_summarizer(ctx, tool):
    await tool(ctx, {"url": "https://example.com"})

    text_passed = tool.summarizer.summarize.call_args[0][0]
    assert "Site navigation" not in text_passed


async def test_header_and_footer_stripped_before_summarizer(ctx, tool):
    await tool(ctx, {"url": "https://example.com"})

    text_passed = tool.summarizer.summarize.call_args[0][0]
    assert "Site header" not in text_passed
    assert "Site footer" not in text_passed


async def test_main_content_passed_to_summarizer(ctx, tool):
    await tool(ctx, {"url": "https://example.com"})

    text_passed = tool.summarizer.summarize.call_args[0][0]
    assert "Welcome to the test page." in text_passed


# ---------------------------------------------------------------------------
# Query forwarding
# ---------------------------------------------------------------------------


async def test_query_forwarded_to_summarizer(ctx, tool):
    await tool(ctx, {"url": "https://example.com", "query": "what is this"})

    _, query_arg = tool.summarizer.summarize.call_args[0]
    assert query_arg == "what is this"


async def test_empty_query_forwarded_to_summarizer(ctx, tool):
    await tool(ctx, {"url": "https://example.com", "query": ""})

    _, query_arg = tool.summarizer.summarize.call_args[0]
    assert query_arg == ""


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


async def test_fetch_failure_returns_error(ctx, tool):
    tool._get = MagicMock(side_effect=Exception("connection refused"))

    result = await tool(ctx, {"url": "https://example.com"})

    assert result.error
    assert "error occurred" in result.output.lower()


async def test_summarizer_failure_falls_back_to_raw_text(ctx, tool):
    tool.summarizer.summarize.side_effect = Exception("LLM unavailable")

    result = await tool(ctx, {"url": "https://example.com"})

    assert not result.error
    assert result.output


async def test_nothing_found_passes_through(ctx, tool):
    tool.summarizer.summarize.return_value = ("NOTHING FOUND", [])

    result = await tool(ctx, {"url": "https://example.com", "query": "obscure"})

    assert not result.error
    assert "NOTHING FOUND" in result.output


# ---------------------------------------------------------------------------
# Browser type
# ---------------------------------------------------------------------------


def test_invalid_browser_type_defaults_to_chrome(tool):
    tool._set_browser_options("lynx")
    assert tool._browser_type == "chrome"


def test_firefox_type_accepted(tool):
    tool._set_browser_options("firefox")
    assert tool._browser_type == "firefox"


def test_chrome_uses_headless_new_arg(tool):
    tool._set_browser_options("chrome")
    assert any("--headless=new" in a for a in tool.options.arguments)


def test_firefox_uses_headless_arg(tool):
    tool._set_browser_options("firefox")
    assert any("--headless" in a for a in tool.options.arguments)
