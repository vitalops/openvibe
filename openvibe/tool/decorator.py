"""@tool decorator — wrap a plain Python function as an openvibe Tool.

Usage::

    from openvibe import tool

    @tool
    def search_jira(query: str, project: str = "ENG") -> str:
        \"\"\"Search Jira tickets matching a query.\"\"\"
        tickets = jira.search(f"project={project} AND text~'{query}'")
        return "\\n".join(f"[{t.key}] {t.summary}" for t in tickets)

Rules:
* Type hints on parameters become the JSON schema sent to the LLM.
* The docstring (first line) becomes the tool description.
* The function must return a plain ``str`` — that string is the tool output.
* Both sync and async functions are supported.
* The resulting object is a ready-to-use ``Tool`` instance.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, get_type_hints

from pydantic import BaseModel, create_model

from openvibe.tool.base import Tool, ToolContext, ToolResult


class _StrictBase(BaseModel):
    model_config = {"extra": "forbid"}


def tool(fn: Callable) -> Tool:
    """Decorate a plain Python function to create an openvibe ``Tool``.

    The decorated function is replaced by a ``Tool`` instance that can be
    passed to ``OpenVibe(tools=[...])``.

    Args:
        fn: A sync or async callable. All parameters must have type hints.
            The return type should be ``str``.

    Returns:
        A ``Tool`` instance whose name matches the function name.
    """
    hints = get_type_hints(fn)
    sig = inspect.signature(fn)

    # Build (annotation, default) pairs for pydantic.create_model.
    # Required params use Ellipsis; params with defaults carry the default.
    fields: dict[str, Any] = {}
    for param_name, param in sig.parameters.items():
        annotation = hints.get(param_name, str)
        if param.default is inspect.Parameter.empty:
            fields[param_name] = (annotation, ...)
        else:
            fields[param_name] = (annotation, param.default)

    DynamicParams: type[BaseModel] = create_model(
        "Params",
        __base__=_StrictBase,
        **fields,
    )

    is_async = asyncio.iscoroutinefunction(fn)
    _name = fn.__name__
    _description = (inspect.getdoc(fn) or _name).split("\n")[0].strip()

    class _WrappedTool(Tool):
        name = _name
        description = _description
        Params = DynamicParams

        async def execute(self, ctx: ToolContext, params: BaseModel) -> ToolResult:
            kwargs = params.model_dump()
            if is_async:
                result = await fn(**kwargs)
            else:
                result = await asyncio.to_thread(fn, **kwargs)
            return ToolResult(title=self.name, output=str(result))

    _WrappedTool.__name__ = _name
    _WrappedTool.__qualname__ = fn.__qualname__

    return _WrappedTool()
