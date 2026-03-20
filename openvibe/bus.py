"""In-process async event bus.

Thin pub/sub layer that decouples producers (session processor, tool runner)
from consumers (HTTP SSE endpoints, logging, metrics).

Every published event is broadcast to all active subscribers. Filtering by
session or event type happens on the consumer side.

Usage::

    bus = EventBus()

    # publish from anywhere
    await bus.publish(SessionUpdatedEvent(session_id="ses_abc", title="New title"))

    # consume inside an SSE handler or background task
    async with bus.subscribe() as events:
        async for event in events:
            if isinstance(event, SessionUpdatedEvent):
                yield event.model_dump_json()
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    """Base dataclass for all bus events.

    Subclass this for every distinct event type::

        @dataclass
        class SessionCreatedEvent(Event):
            title: str
    """
    session_id: str | None = field(default=None)


class EventBus:
    """Broadcast-style async event bus.

    All subscribers receive all events. Use ``isinstance`` checks or compare
    ``event.session_id`` to filter in the consuming loop.
    """

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[Any | None]] = []

    async def publish(self, event: Any) -> None:
        """Broadcast *event* to every active subscriber."""
        for q in list(self._queues):
            await q.put(event)

    @asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[AsyncGenerator[Any, None], None]:
        """Async context manager yielding an async generator of events.

        The subscription is removed and the generator closed automatically
        when the ``async with`` block exits::

            async with bus.subscribe() as events:
                async for event in events:
                    process(event)
        """
        queue: asyncio.Queue[Any | None] = asyncio.Queue()
        self._queues.append(queue)

        async def _stream() -> AsyncGenerator[Any, None]:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item

        try:
            yield _stream()
        finally:
            if queue in self._queues:
                self._queues.remove(queue)
            await queue.put(None)  # unblock any waiting _stream consumer
