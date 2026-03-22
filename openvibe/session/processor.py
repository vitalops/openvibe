"""Session processor — the core agent execution loop.

This module orchestrates the back-and-forth between the user, the LLM, and
the tool system.  A single call to ``run()`` handles one complete user turn:

1. Save the user message to the DB and publish an event.
2. Build the full message history for the LLM (system prompt + history).
3. Stream the LLM response, updating message parts in real-time.
4. Execute any tool calls (permission-checked, doom-loop-detected).
5. Feed tool results back and loop until the model stops calling tools.
6. Save final state and publish a TurnCompleted event.

Doom-loop detection
-------------------
If the same (tool, arguments) pair is called 3 or more times in a single
turn, ``DoomLoopWarning`` is published and the loop is broken.

Context overflow
----------------
When the LLM raises a context-length error, a ``ContextOverflowError`` is
stored on the message and ``CompactionNeeded`` is published so the caller
can trigger compaction (see ``compaction.py``).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openvibe.llm import (
    LLMBackend,
    Message,
    ReasoningDelta,
    StreamDone,
    TextDelta,
    ToolCallBegin,
    ToolCallComplete,
    ToolCallDelta,
    ToolDefinition,
)
from openvibe.session import session as session_store
from openvibe.config import MessageRole, ToolStateStatus
from openvibe.session.models import (
    APIError,
    AssistantError,
    AuthError,
    ContextOverflowError,
    MessageInfo,
    PartUpdatedEvent,
    ReasoningDeltaEvent,
    ReasoningPart,
    SessionInfo,
    StepStartPart,
    TextDeltaEvent,
    TextPart,
    ToolPart,
    ToolState,
    ToolStateChangedEvent,
    TurnCompletedEvent,
    now_iso,
)
from openvibe.tool.base import Tool, ToolContext, ToolRegistry, ToolResult

if TYPE_CHECKING:
    from openvibe.agent.agent import AgentInfo
    from openvibe.bus import EventBus
    from openvibe.db import Database
    from openvibe.permission.permission import PermissionService, Rule

DOOM_LOOP_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Processor events (distinct from session model events)
# ---------------------------------------------------------------------------


@dataclass
class CompactionNeeded:
    session_id: str


@dataclass
class DoomLoopWarning:
    session_id: str
    tool_name: str
    call_count: int


# ---------------------------------------------------------------------------
# Main processor class
# ---------------------------------------------------------------------------


class SessionProcessor:
    """Runs one user turn through the full agent loop."""

    def __init__(
        self,
        db: "Database",
        llm: LLMBackend,
        bus: "EventBus",
        registry: ToolRegistry,
        permissions: "PermissionService",
    ) -> None:
        self._db = db
        self._llm = llm
        self._bus = bus
        self._registry = registry
        self._permissions = permissions

    async def run(
        self,
        session: SessionInfo,
        agent: "AgentInfo",
        user_text: str,
        abort: asyncio.Event | None = None,
        user_message: MessageInfo | None = None,
    ) -> MessageInfo:
        """Process one user turn; returns the completed assistant MessageInfo."""
        abort = abort or asyncio.Event()

        # 1. Persist user message (or use one already created + displayed by the TUI)
        from openvibe.session.models import MessageCreatedEvent

        if user_message is not None:
            user_msg = user_message
        else:
            user_msg = session_store.add_message(
                self._db,
                session.id,
                MessageRole.USER,
                [TextPart(content=user_text)],
            )
            await self._bus.publish(
                MessageCreatedEvent(session_id=session.id, message=user_msg)
            )

        # 2. Build LLM message history
        history = session_store.list_messages(self._db, session.id)

        # 3. Construct tools list for this agent
        tool_defs = _build_tool_definitions(self._registry, agent)

        # 4. Permission rules for this agent
        rules: list["Rule"] = list(agent.permission_rules)

        # 5. Main loop
        doom_counts: dict[str, int] = {}  # key: "tool:args_json" → count
        assistant_msg: MessageInfo | None = None

        for _iteration in range(agent.max_steps or 50):
            if abort.is_set():
                break

            assistant_msg = await self._run_single_iteration(
                session=session,
                agent=agent,
                history=history,
                tool_defs=tool_defs,
                rules=rules,
                doom_counts=doom_counts,
                abort=abort,
            )

            # Check if we should continue
            last_part_has_tool = any(
                isinstance(p, ToolPart) for p in assistant_msg.parts
            )
            if not last_part_has_tool:
                break

            # Reload history for next iteration (includes tool results)
            history = session_store.list_messages(self._db, session.id)

        final_msg = assistant_msg or session_store.add_message(
            self._db, session.id, MessageRole.ASSISTANT, [TextPart(content="")]
        )

        await self._bus.publish(
            TurnCompletedEvent(session_id=session.id, message_id=final_msg.id)
        )
        return final_msg

    async def _run_single_iteration(
        self,
        session: SessionInfo,
        agent: "AgentInfo",
        history: list[MessageInfo],
        tool_defs: list[ToolDefinition],
        rules: list["Rule"],
        doom_counts: dict[str, int],
        abort: asyncio.Event,
    ) -> MessageInfo:
        # Create the assistant message shell up-front so we have an ID, but
        # don't announce it to the bus yet — wait until first real content so
        # a failed LLM call doesn't leave an empty "assistant" widget in the UI.
        assistant_msg = session_store.add_message(
            self._db, session.id, MessageRole.ASSISTANT
        )
        assistant_msg.parts = [StepStartPart()]
        session_store.upsert_part(self._db, assistant_msg.id, 0, StepStartPart())
        from openvibe.session.models import MessageCreatedEvent

        announced = False

        async def _announce() -> None:
            nonlocal announced
            if not announced:
                announced = True
                await self._bus.publish(
                    MessageCreatedEvent(session_id=session.id, message=assistant_msg)
                )

        ll_messages = _to_llm_messages(history, agent)
        system_prompt = _build_system_prompt(agent)

        text_part_index: int | None = None
        reasoning_part_index: int | None = None
        tool_part_indices: dict[int, int] = {}  # llm_index → parts_index

        t0 = time.monotonic()

        try:
            async for event in self._llm.stream(
                model=_model_string(agent),
                messages=ll_messages,
                tools=tool_defs or None,
                system=system_prompt,
                temperature=agent.temperature,
                top_p=agent.top_p,
            ):
                if abort.is_set():
                    break

                match event:
                    case TextDelta(content=content):
                        await _announce()
                        text_part_index = await self._append_text(
                            assistant_msg, text_part_index, content, t0
                        )
                        await self._bus.publish(
                            TextDeltaEvent(
                                session_id=session.id,
                                message_id=assistant_msg.id,
                                content=content,
                            )
                        )

                    case ReasoningDelta(content=content):
                        await _announce()
                        reasoning_part_index = await self._append_reasoning(
                            assistant_msg, reasoning_part_index, content, t0
                        )
                        await self._bus.publish(
                            ReasoningDeltaEvent(
                                session_id=session.id,
                                message_id=assistant_msg.id,
                                content=content,
                            )
                        )

                    case ToolCallBegin(index=idx, id=call_id, name=name):
                        await _announce()
                        part_idx = len(assistant_msg.parts)
                        tool_part_indices[idx] = part_idx
                        tool_part = ToolPart(
                            state=ToolState(
                                status=ToolStateStatus.PENDING,
                                call_id=call_id,
                                tool_name=name,
                                time_start=time.monotonic() - t0,
                            )
                        )
                        assistant_msg.parts.append(tool_part)
                        session_store.upsert_part(
                            self._db, assistant_msg.id, part_idx, tool_part
                        )
                        await self._bus.publish(
                            ToolStateChangedEvent(
                                session_id=session.id,
                                message_id=assistant_msg.id,
                                part_index=part_idx,
                                state=tool_part.state.model_dump(),
                            )
                        )

                    case ToolCallDelta():
                        pass  # args accumulate inside ToolCallComplete

                    case ToolCallComplete(
                        index=idx, id=call_id, name=name, arguments=args
                    ):
                        part_idx = tool_part_indices.get(
                            idx, len(assistant_msg.parts) - 1
                        )
                        try:
                            parsed_args = json.loads(args) if args.strip() else {}
                        except json.JSONDecodeError:
                            parsed_args = {"_raw": args}

                        # Update tool part to running
                        tool_part = assistant_msg.parts[part_idx]
                        assert isinstance(tool_part, ToolPart)
                        tool_part.state.status = ToolStateStatus.RUNNING
                        tool_part.state.call_id = call_id
                        tool_part.state.tool_name = name
                        tool_part.state.input = parsed_args
                        session_store.upsert_part(
                            self._db, assistant_msg.id, part_idx, tool_part
                        )

                        # Doom-loop check
                        doom_key = f"{name}:{args}"
                        doom_counts[doom_key] = doom_counts.get(doom_key, 0) + 1
                        if doom_counts[doom_key] >= DOOM_LOOP_THRESHOLD:
                            await self._bus.publish(
                                DoomLoopWarning(
                                    session_id=session.id,
                                    tool_name=name,
                                    call_count=doom_counts[doom_key],
                                )
                            )
                            tool_part.state.status = ToolStateStatus.ERROR
                            tool_part.state.error = (
                                f"Doom loop detected: '{name}' called with identical "
                                f"arguments {doom_counts[doom_key]} times."
                            )
                            session_store.upsert_part(
                                self._db, assistant_msg.id, part_idx, tool_part
                            )
                            continue

                        # Execute the tool
                        result = await self._execute_tool(
                            name=name,
                            args=parsed_args,
                            call_id=call_id,
                            session=session,
                            agent=agent,
                            assistant_msg_id=assistant_msg.id,
                            rules=rules,
                            abort=abort,
                        )

                        # Store result on the tool part
                        tool_part.state.status = (
                            ToolStateStatus.ERROR
                            if result.error
                            else ToolStateStatus.COMPLETED
                        )
                        tool_part.state.output = result.output
                        tool_part.state.time_end = time.monotonic() - t0
                        if result.error:
                            tool_part.state.error = result.output
                        session_store.upsert_part(
                            self._db, assistant_msg.id, part_idx, tool_part
                        )

                        # Also add a tool-result message for the LLM's next turn
                        session_store.add_message(
                            self._db,
                            session.id,
                            MessageRole.ASSISTANT,  # stored as a follow-up; LLM sees it as "tool"
                        )
                        # (We embed the raw result text; the LLM message builder
                        #  will translate this to role="tool" when building the next prompt.)
                        _store_tool_result(
                            self._db,
                            session.id,
                            call_id,
                            name,
                            result.output,
                            result.error,
                        )

                        await self._bus.publish(
                            ToolStateChangedEvent(
                                session_id=session.id,
                                message_id=assistant_msg.id,
                                part_index=part_idx,
                                state=tool_part.state.model_dump(),
                            )
                        )

                    case StreamDone(
                        input_tokens=inp,
                        output_tokens=out,
                        cache_read_tokens=cr,
                        cache_write_tokens=cw,
                    ):
                        session_store.update_cost(
                            self._db,
                            session.id,
                            cost=0.0,  # TODO: compute from model pricing
                            input_tokens=inp,
                            output_tokens=out,
                            cache_read_tokens=cr,
                            cache_write_tokens=cw,
                        )

        except Exception as exc:
            error = _classify_error(exc)
            assistant_msg.error = error
            raise

        return assistant_msg

    async def resume_interrupted(
        self,
        session: SessionInfo,
        agent: "AgentInfo",
        allow: bool,
        abort: asyncio.Event | None = None,
    ) -> MessageInfo:
        """Execute (or deny) interrupted tool calls, then get the LLM's final response.

        Used when the app is restarted after being closed mid-permission.
        The user has already made their choice, so the tool is executed
        directly without going through the permission service.
        """
        import time as _time

        abort = abort or asyncio.Event()
        history = session_store.list_messages(self._db, session.id)
        last_user_msg: MessageInfo | None = None

        for msg in history:
            if msg.role == MessageRole.USER:
                last_user_msg = msg
            if msg.role != MessageRole.ASSISTANT:
                continue
            for i, part in enumerate(msg.parts):
                if (
                    not isinstance(part, ToolPart)
                    or not part.state.call_id
                    or part.state.output is not None
                ):
                    continue

                if allow:
                    tool = self._registry.get(part.state.tool_name)
                    if tool:
                        from openvibe.tool.base import ToolContext

                        ctx = ToolContext(
                            session_id=session.id,
                            message_id=msg.id,
                            agent_name=agent.name,
                            project_id=session.project_id,
                            working_dir=session.directory,
                            abort=abort,
                            call_id=part.state.call_id,
                            _permissions=None,  # user already approved; bypass check
                        )
                        ctx._db = self._db  # type: ignore[attr-defined]
                        t0 = _time.monotonic()
                        try:
                            result = await tool(ctx, part.state.input)
                            part.state.status = (
                                ToolStateStatus.ERROR
                                if result.error
                                else ToolStateStatus.COMPLETED
                            )
                            part.state.output = result.output
                            part.state.time_end = _time.monotonic() - t0
                            if result.error:
                                part.state.error = result.output
                        except Exception as exc:
                            part.state.status = ToolStateStatus.ERROR
                            part.state.output = str(exc)
                            part.state.error = str(exc)
                    else:
                        part.state.status = ToolStateStatus.ERROR
                        part.state.output = f"Tool '{part.state.tool_name}' not found."
                        part.state.error = part.state.output
                else:
                    part.state.status = ToolStateStatus.ERROR
                    part.state.output = "Permission denied."
                    part.state.error = "Permission denied."

                session_store.upsert_part(self._db, msg.id, i, part)
                await self._bus.publish(
                    ToolStateChangedEvent(
                        session_id=session.id,
                        message_id=msg.id,
                        part_index=i,
                        state=part.state.model_dump(),
                    )
                )

        if last_user_msg is None:
            final_msg = session_store.add_message(
                self._db, session.id, MessageRole.ASSISTANT, [TextPart(content="")]
            )
            await self._bus.publish(
                TurnCompletedEvent(session_id=session.id, message_id=final_msg.id)
            )
            return final_msg

        # Continue the LLM turn using the existing last user message as anchor.
        # The updated tool results are now in the DB — no new user message is created.
        return await self.run(session, agent, "", abort, user_message=last_user_msg)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _append_text(
        self,
        msg: MessageInfo,
        part_idx: int | None,
        content: str,
        t0: float,
    ) -> int:
        existing = (
            msg.parts[part_idx]
            if part_idx is not None and part_idx < len(msg.parts)
            else None
        )
        if not isinstance(existing, TextPart):
            part_idx = len(msg.parts)
            part = TextPart(content=content, time_start=time.monotonic() - t0)
            msg.parts.append(part)
        else:
            part = existing
            part.content += content

        session_store.upsert_part(self._db, msg.id, part_idx, msg.parts[part_idx])
        return part_idx

    async def _append_reasoning(
        self,
        msg: MessageInfo,
        part_idx: int | None,
        content: str,
        t0: float,
    ) -> int:
        if part_idx is None or not isinstance(
            (
                msg.parts[part_idx]
                if part_idx is not None and part_idx < len(msg.parts)
                else None
            ),
            ReasoningPart,
        ):
            part_idx = len(msg.parts)
            part = ReasoningPart(content=content, time_start=time.monotonic() - t0)
            msg.parts.append(part)
        else:
            part = msg.parts[part_idx]
            assert isinstance(part, ReasoningPart)
            part.content += content

        session_store.upsert_part(self._db, msg.id, part_idx, msg.parts[part_idx])
        return part_idx

    async def _execute_tool(
        self,
        name: str,
        args: dict[str, Any],
        call_id: str,
        session: SessionInfo,
        agent: "AgentInfo",
        assistant_msg_id: str,
        rules: list["Rule"],
        abort: asyncio.Event,
    ) -> "ToolResult":
        from openvibe.tool.base import ToolResult

        tool = self._registry.get(name)
        if not tool:
            return ToolResult(
                title=f"Unknown tool: {name}",
                output=f"Tool '{name}' is not registered.",
                error=True,
            )

        ctx = ToolContext(
            session_id=session.id,
            message_id=assistant_msg_id,
            agent_name=agent.name,
            project_id=session.project_id,
            working_dir=session.directory,
            abort=abort,
            call_id=call_id,
            _permissions=self._permissions,
        )
        # Inject DB reference for tools that need it (todo, etc.)
        ctx._db = self._db  # type: ignore[attr-defined]

        try:
            return await tool(ctx, args)
        except Exception as exc:
            from openvibe.permission.permission import (
                PermissionDenied,
                PermissionRejected,
            )

            if isinstance(exc, (PermissionDenied, PermissionRejected)):
                return ToolResult(
                    title=f"Permission denied: {name}",
                    output=str(exc),
                    error=True,
                )
            return ToolResult(title=f"Error in {name}", output=str(exc), error=True)


# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------


def _build_tool_definitions(
    registry: ToolRegistry, agent: "AgentInfo"
) -> list[ToolDefinition]:
    disabled = set(agent.disabled_tools or [])
    return [
        ToolDefinition(
            name=t.name,
            description=t.description,
            parameters=t.parameters_schema(),
        )
        for t in registry.all()
        if t.name not in disabled
    ]


def _build_system_prompt(agent: "AgentInfo") -> str:
    parts = [agent.system_prompt]
    if agent.extra_instructions:
        parts.extend(agent.extra_instructions)
    return "\n\n".join(filter(None, parts))


def _model_string(agent: "AgentInfo") -> str:
    """Return the litellm model string, e.g. 'anthropic/claude-sonnet-4-5'."""
    if agent.model:
        return f"{agent.model.provider_id}/{agent.model.model_id}"
    return "azure/gpt-4.1"  # sensible default


def _to_llm_messages(history: list[MessageInfo], agent: "AgentInfo") -> list[Message]:
    """Convert stored MessageInfo objects to LLM Message objects."""
    from openvibe.llm import ContentBlock, Message

    messages: list[Message] = []
    for msg in history:
        if msg.role in (MessageRole.ERROR, MessageRole.PERMISSION):
            continue
        if msg.role == MessageRole.USER:
            text = _extract_text(msg)
            messages.append(Message(role="user", content=text))

        elif msg.role == MessageRole.ASSISTANT:
            text_parts = [p for p in msg.parts if isinstance(p, TextPart)]
            tool_parts = [p for p in msg.parts if isinstance(p, ToolPart)]

            if text_parts or tool_parts:
                content = "\n".join(p.content for p in text_parts).strip()
                tool_calls = [
                    {
                        "id": p.state.call_id,
                        "type": "function",
                        "function": {
                            "name": p.state.tool_name,
                            "arguments": json.dumps(p.state.input),
                        },
                    }
                    for p in tool_parts
                    if p.state.call_id
                ]
                messages.append(
                    Message(role="assistant", content=content, tool_calls=tool_calls)
                )

                # Add tool results as separate tool messages.
                # If a tool was interrupted (app quit while permission was pending),
                # output is None — emit a synthetic result so the LLM message
                # sequence remains valid (every tool_call needs a matching result).
                for part in tool_parts:
                    content = (
                        part.state.output
                        if part.state.output is not None
                        else "Tool execution was interrupted (session was closed before the tool completed)."
                    )
                    messages.append(
                        Message(
                            role="tool",
                            content=content,
                            tool_call_id=part.state.call_id,
                        )
                    )

    return messages


def _extract_text(msg: MessageInfo) -> str:
    return "\n".join(p.content for p in msg.parts if isinstance(p, TextPart)).strip()


def _store_tool_result(
    db: "Database",
    session_id: str,
    call_id: str,
    tool_name: str,
    output: str,
    error: bool,
) -> None:
    """Persist a tool result as a standalone message for history reconstruction."""
    # We store tool results embedded in the ToolPart state, so this is a no-op
    # here — but it's a hook for custom backends that need separate storage.
    pass


def _classify_error(exc: Exception) -> "AssistantError":
    msg = str(exc).lower()
    if "context" in msg and ("length" in msg or "window" in msg or "limit" in msg):
        return ContextOverflowError()
    if "auth" in msg or "api key" in msg or "unauthorized" in msg:
        return AuthError(message=str(exc))
    return APIError(message=str(exc))
