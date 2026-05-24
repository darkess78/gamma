from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .models import PerformerOutputEvent


@dataclass(slots=True)
class _Subscriber:
    subscriber_id: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any]]


class PerformerEventBus:
    def __init__(self, *, history_limit: int = 200, subscriber_queue_size: int = 100) -> None:
        self._history: deque[PerformerOutputEvent] = deque(maxlen=max(1, history_limit))
        self._subscribers: dict[str, _Subscriber] = {}
        self._subscriber_queue_size = max(1, subscriber_queue_size)
        self._lock = threading.Lock()

    def publish(self, event: PerformerOutputEvent) -> None:
        with self._lock:
            self._history.append(event)
            subscribers = list(self._subscribers.values())
        payload = event.model_dump()
        for subscriber in subscribers:
            subscriber.loop.call_soon_threadsafe(self._put_nowait, subscriber.queue, payload)

    def recent(self, *, limit: int = 50) -> list[PerformerOutputEvent]:
        with self._lock:
            items = list(self._history)
        return items[-max(1, limit):]

    async def subscribe(self, *, replay_recent: int = 0) -> tuple[str, asyncio.Queue[dict[str, Any]]]:
        subscriber_id = uuid4().hex
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._subscriber_queue_size)
        subscriber = _Subscriber(subscriber_id=subscriber_id, loop=asyncio.get_running_loop(), queue=queue)
        with self._lock:
            self._subscribers[subscriber_id] = subscriber
            recent = list(self._history)[-max(0, replay_recent):] if replay_recent > 0 else []
        for event in recent:
            self._put_nowait(queue, event.model_dump())
        return subscriber_id, queue

    def unsubscribe(self, subscriber_id: str) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "subscriber_count": len(self._subscribers),
                "history_count": len(self._history),
            }

    @staticmethod
    def _put_nowait(queue: asyncio.Queue[dict[str, Any]], payload: dict[str, Any]) -> None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass


_BUS = PerformerEventBus()


def get_performer_event_bus() -> PerformerEventBus:
    return _BUS
