# Custom Tools

Any Python class that subclasses `Tool` and implements `execute()` can be registered in the tool registry and made available to the agent.

## Anatomy of a tool

```python
from pydantic import Field
from openvibe.tool.base import Tool, ToolContext, ToolResult


class MyTool(Tool):
    name = "my_tool"          # identifier the LLM uses to call the tool
    description = (           # shown to the LLM in the tool schema
        "Does something useful. "
        "Be specific — the LLM uses this to decide when to call the tool."
    )

    class Params(Tool.Params):
        """Parameters the LLM must supply when calling this tool."""
        message: str = Field(description="The message to process.")
        count: int = Field(default=1, ge=1, description="How many times to process it.")

    async def execute(self, ctx: ToolContext, params: "MyTool.Params") -> ToolResult:
        # Check permission before doing anything impactful
        await ctx.check_permission(
            tool=self.name,
            argument=params.message,
            description=f"Process '{params.message}'",
        )

        # Implement the tool logic
        result = params.message.upper() * params.count

        return ToolResult(
            title=f"MyTool: {params.message}",
            output=result,
        )
```

## ToolContext

`ctx` is injected at call time and provides:

| Attribute | Type | Description |
|-----------|------|-------------|
| `ctx.session_id` | `str` | ID of the current session |
| `ctx.project_id` | `str` | ID of the current project |
| `ctx.working_dir` | `str` | Project root directory (absolute path) |
| `ctx.agent_name` | `str` | Name of the active agent |
| `ctx.message_id` | `str` | ID of the current assistant message |
| `ctx.abort` | `asyncio.Event` | Set when the user cancels the turn |
| `ctx.check_permission(tool, argument, description)` | coroutine | Raises `PermissionDenied` / `PermissionRejected` if not allowed |

Always call `ctx.check_permission()` before any action that modifies state, sends data externally, or could be destructive.

## ToolResult

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `title` | `str` | — | Short label shown in the TUI tool widget |
| `output` | `str` | — | Text returned to the LLM. Auto-truncated at 4,000 characters. |
| `error` | `bool` | `False` | Set `True` to indicate failure |
| `metadata` | `dict` | `{}` | Arbitrary metadata. Set `{"truncated": True}` to disable auto-truncation. |
| `attachments` | `list[Attachment]` | `[]` | Binary or text attachments |

## Registering a tool

### In a script or application

```python
from openvibe import OpenVibe
from openvibe.tool.base import create_default_registry

registry = create_default_registry()
registry.register(MyTool())

with OpenVibe() as ov:
    ov._registry = registry
    session = ov.create_session()
    session.send("Use my_tool to process 'hello world'")
```

### In `skills/<name>.py` (auto-loaded)

Files in `<project>/skills/` are loaded automatically. You can register tools there too:

```python
# skills/custom_tools.py
from openvibe.tool.base import Tool, ToolContext, ToolResult
# ... define MyTool ...

# Tools registered here are available in the same process
# but the registry injection pattern above is more reliable
```

## Cancellation

For long-running tools, check `ctx.abort` periodically:

```python
async def execute(self, ctx: ToolContext, params: "MyTool.Params") -> ToolResult:
    for item in large_dataset:
        if ctx.abort.is_set():
            return ToolResult(title="MyTool", output="Cancelled.", error=True)
        process(item)
    return ToolResult(title="MyTool", output="Done.")
```

Implement `cancel()` to clean up external resources:

```python
def cancel(self) -> None:
    if self._proc:
        self._proc.terminate()
```

## Complete example — Slack tool

```python
from pydantic import Field
from openvibe.tool.base import Tool, ToolContext, ToolResult


class SlackTool(Tool):
    name = "slack"
    description = (
        "Post a message to a Slack channel. "
        "Use when the user asks to notify the team, send a status update, "
        "or post anything to Slack."
    )

    class Params(Tool.Params):
        channel: str = Field(
            description="Channel name including #, e.g. #general or #dev-alerts"
        )
        message: str = Field(description="Message text to post.")

    async def execute(self, ctx: ToolContext, params: "SlackTool.Params") -> ToolResult:
        await ctx.check_permission(
            tool=self.name,
            argument=params.channel,
            description=f"Post to Slack {params.channel}",
        )

        import httpx
        import os

        token = os.environ.get("SLACK_BOT_TOKEN")
        if not token:
            return ToolResult(
                title=f"Slack → {params.channel}",
                output="SLACK_BOT_TOKEN not set.",
                error=True,
            )

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": params.channel, "text": params.message},
            )
            data = resp.json()

        if not data.get("ok"):
            return ToolResult(
                title=f"Slack → {params.channel}",
                output=f"Slack API error: {data.get('error', 'unknown')}",
                error=True,
            )

        return ToolResult(
            title=f"Slack → {params.channel}",
            output=f"Message posted to {params.channel}.",
        )
```

## Params model tips

- Use `pydantic.Field(description=...)` on every field — the description goes into the JSON Schema that the LLM sees.
- Use `ge`, `le`, `min_length`, `max_length`, `pattern` validators to constrain inputs.
- Use `Literal` types for enumerated options: `action: Literal["read", "write", "delete"]`.
- The inner class must be named `Params` and inherit from `Tool.Params` (which sets `extra="forbid"`).

```python
from typing import Literal
from pydantic import Field

class Params(Tool.Params):
    action: Literal["read", "write"] = Field(description="Operation to perform.")
    path: str = Field(description="Absolute file path.")
    content: str | None = Field(default=None, description="Content to write (write action only).")
```
