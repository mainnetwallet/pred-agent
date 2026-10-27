"""In-process event bus.

Grounded in kit/03_ARCHITECTURE.md section 6 (Concurrency model).

Persist first, then publish events. A crash must never lose an order state transition.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Event:
    type: str
    payload: dict[str, Any]


class EventBus:
    """Simple asyncio.Queue-based pub/sub bus for one process."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[Event]]] = defaultdict(list)
        self._queues: list[asyncio.Queue[Event]] = []

    def subscribe(self, event_type: str, queue: asyncio.Queue[Event]) -> None:
        self._subscribers[event_type].append(queue)
        self._queues.append(queue)

    def unsubscribe(self, event_type: str, queue: asyncio.Queue[Event]) -> None:
        self._subscribers[event_type].remove(queue)
        if queue in self._queues:
            self._queues.remove(queue)

    async def publish(self, event: Event) -> None:
        queues = self._subscribers.get(event.type, [])
        for q in queues:
            # Non-blocking put; drop if a subscriber is too slow (alerting layer handles this)
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def drain(self, queue: asyncio.Queue[Event]) -> Event:
        return await queue.get()
