"""In-process async event bus for WebSocket fan-out.

N038: every publish path validates type/provenance and redacts secrets
*before* history or subscriber queues. Rejected events never fan out.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from windows_os_api.core.events.schema import validate_and_sanitize


@dataclass
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: float = field(default_factory=time.time)
    provenance: str = "internal"


class EventBus:
    def __init__(self) -> None:
        self._subs: list[asyncio.Queue[Event]] = []
        self._history: list[Event] = []
        self._max_history = 500
        self._lock = asyncio.Lock()
        self._last_reject_reason: str | None = None

    @property
    def last_reject_reason(self) -> str | None:
        return self._last_reject_reason

    def _accept(
        self,
        event_type: str,
        payload: dict[str, Any] | None,
        *,
        provenance: str,
        event_id: str | None = None,
        ts: float | None = None,
    ) -> Event | None:
        clean, reason = validate_and_sanitize(
            event_type, payload, provenance=provenance
        )
        if reason is not None:
            self._last_reject_reason = reason
            return None
        self._last_reject_reason = None
        kwargs: dict[str, Any] = {
            "type": event_type,
            "payload": clean or {},
            "provenance": provenance,
        }
        if event_id is not None:
            kwargs["id"] = event_id
        if ts is not None:
            kwargs["ts"] = ts
        return Event(**kwargs)

    def _fan_out(self, event: Event) -> None:
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def publish(self, event: Event) -> Event | None:
        accepted = self._accept(
            event.type,
            event.payload,
            provenance=getattr(event, "provenance", "internal") or "internal",
            event_id=event.id,
            ts=event.ts,
        )
        if accepted is None:
            return None
        self._fan_out(accepted)
        return accepted

    def publish_sync(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        provenance: str = "internal",
    ) -> Event | None:
        accepted = self._accept(event_type, payload, provenance=provenance)
        if accepted is None:
            return None
        self._fan_out(accepted)
        return accepted

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


def reset_event_bus() -> None:
    """Drop the process-wide bus so tests cannot inherit history/subscribers."""
    global _bus
    _bus = None
