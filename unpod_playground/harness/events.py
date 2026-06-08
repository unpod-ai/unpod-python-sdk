"""Side-channel event bus — relays agent-process events to the browser.

Per the PRD, transcript / metrics / flow-node events ride the harness's *own*
side-channel (session hooks → browser), not the supervoice audio bridge. For M1
(single agent, single active session) this is a simple in-process broadcast bus:
session hooks call :meth:`EventBus.publish`; each connected side-channel
WebSocket drains its own subscription queue.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any, Iterator

_QUEUE_MAXSIZE = 256


class EventBus:
    """In-process fan-out of supervoice-named events to WS subscribers."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def publish(self, event_type: str, **data: Any) -> None:
        """Broadcast an event to every current subscriber (drop if full)."""
        message = {"type": event_type, "data": data}
        for queue in self._subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[dict[str, Any]]]:
        """Register a subscription queue for the lifetime of the context."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
