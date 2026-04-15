"""WebFetch tool — fetch and extract text from a URL."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
from pydantic import Field

from openvibe.tool.base import MAX_OUTPUT_CHARS, Tool, ToolContext, ToolResult

# Simple HTML tag stripper (good enough for most pages; no heavy dependencies)
_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_NL = re.compile(r"\n{3,}")
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)


def _html_to_text(html: str) -> str:
    text = _SCRIPT_STYLE_RE.sub("", html)
    text = _TAG_RE.sub("", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&#\d+;", "", text)
    text = _MULTI_NL.sub("\n\n", text)
    return text.strip()


class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "Fetch a URL and return its text content. HTML pages are stripped "
        "of tags and scripts. Useful for reading documentation, issue "
        "trackers, or any public web resource. Not suitable for pages that "
        "require JavaScript rendering."
    )

    class Params(Tool.Params):
        url: str = Field(description="The URL to fetch.")
        max_chars: int = Field(
            default=MAX_OUTPUT_CHARS,
            ge=100,
            le=50_000,
            description="Maximum characters to return.",
        )

    async def execute(
        self, ctx: ToolContext, params: "WebFetchTool.Params"
    ) -> ToolResult:
        # Basic URL validation
        parsed = urlparse(params.url)
        if parsed.scheme not in ("http", "https"):
            return ToolResult(
                title=f"Fetch {params.url}",
                output="Only http:// and https:// URLs are supported.",
                error=True,
            )

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                response = await client.get(
                    params.url,
                    headers={"User-Agent": "openvibe/0.1 (AI coding agent)"},
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            return ToolResult(
                title=f"Fetch {params.url}", output="Request timed out.", error=True
            )
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                title=f"Fetch {params.url}",
                output=f"HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
                error=True,
            )
        except httpx.RequestError as exc:
            return ToolResult(title=f"Fetch {params.url}", output=str(exc), error=True)

        content_type = response.headers.get("content-type", "")
        if "html" in content_type:
            text = _html_to_text(response.text)
        else:
            text = response.text

        truncated = len(text) > params.max_chars
        return ToolResult(
            title=f"Fetched {params.url}",
            output=text[: params.max_chars],
            metadata={"truncated": truncated, "content_type": content_type},
        )
