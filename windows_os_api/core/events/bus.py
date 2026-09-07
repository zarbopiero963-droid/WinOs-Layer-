"""In-process async event bus for WebSocket fan-out."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable


@dataclass
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: float = field(default_factory=time.time)


class EventBus:
    def __init__(self) -> None:
        self._subs: list[asyncio.Queue[Event]] = []
        self._history: list[Event] = []
        self._max_history = 500
        self._lock = asyncio.Lock()

    async def publish(self, event: Event) -> None:
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def publish_sync(self, event_type: str, payload: dict[str, Any] | None = None) -> Event:
        ev = Event(type=event_type, payload=payload or {})
        self._history.append(ev)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass
        return ev

    async def subscribe(self) -> AsyncIterator[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=256)
        self._subs.append(q)
        try:
            while True:
                ev = await q.get()
                yield ev
        finally:
            if q in self._subs:
                self._subs.remove(q)

    def history(self, limit: int = 50) -> list[Event]:
        return self._history[-limit:]


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
