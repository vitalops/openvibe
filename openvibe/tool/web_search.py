from __future__ import annotations

from ddgs import DDGS
from pydantic import Field

from openvibe.tool.base import Tool, ToolContext, ToolResult

_MAX_RESULTS = 20


def _format_results(results: list[dict]) -> str:
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "(no title)")
        url = r.get("href", "")
        snippet = r.get("body", "").strip()
        lines.append(f"{i}. {title}\n   URL: {url}\n   {snippet}")
    return "\n\n".join(lines)


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web for a query and return a ranked list of results, each "
        "with a title, URL, and short snippet. Use this to find current "
        "information, documentation, news, or any publicly available content. "
        "Follow up with web_fetch to read the full content of a result page."
    )

    class Params(Tool.Params):
        query: str = Field(description="The search query string.")
        num_results: int = Field(
            default=8,
            ge=1,
            le=_MAX_RESULTS,
            description="Number of results to return (1–20). Default: 8.",
        )
        start_page: int = Field(
            default=1,
            ge=1,
            description=(
                "Result page to start from (1-indexed). "
                "Increase to paginate past the first set of results."
            ),
        )

    async def execute(
        self, ctx: ToolContext, params: "WebSearchTool.Params"
    ) -> ToolResult:
        try:
            with DDGS() as ddgs:
                results = ddgs.text(
                    params.query,
                    max_results=params.num_results,
                    page=params.start_page,
                )
        except Exception as exc:
            return ToolResult(
                title=f"Search: {params.query}",
                output=str(exc),
                error=True,
            )

        if not results:
            return ToolResult(
                title=f"Search: {params.query}",
                output="No results found.",
                metadata={"num_results": 0},
            )

        return ToolResult(
            title=f"Search: {params.query}",
            output=_format_results(results),
            metadata={
                "num_results": len(results),
                "start_page": params.start_page,
            },
        )
