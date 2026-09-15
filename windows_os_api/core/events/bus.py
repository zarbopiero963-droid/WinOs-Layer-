"""In-process async event bus for WebSocket fan-out.

N038: every publish path validates type/provenance and redacts secrets
*before* history or subscriber queues. Rejected events never fan out.

N039: thread→loop handoff is non-blocking; queue/subscriber caps produce
*observable* drops; stop/sentinel + reset tear down without deadlock/leak.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from windows_os_api.core.events.schema import validate_and_sanitize

# Sentinel placed on subscriber queues so ``subscribe`` exits cleanly.
_STOP: object = object()

DEFAULT_QUEUE_MAXSIZE = 256
DEFAULT_MAX_HISTORY = 500
DEFAULT_MAX_SUBSCRIBERS = 64
# Soft budget across all subscriber queues (approx UTF-8 JSON of payloads).
DEFAULT_MAX_QUEUED_BYTES = 1_048_576


class BusClosedError(RuntimeError):
    """Raised when subscribe is attempted after stop()."""


class BusAtCapacityError(RuntimeError):
    """Raised when max concurrent subscribers is reached."""


@dataclass
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: float = field(default_factory=time.time)
    provenance: str = "internal"


@dataclass
class BusStats:
    published: int = 0
    rejected: int = 0
    dropped: int = 0
    subscribers: int = 0
    queued_bytes: int = 0
    stopped: bool = False


class EventBus:
    def __init__(
        self,
        *,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        max_history: int = DEFAULT_MAX_HISTORY,
        max_subscribers: int = DEFAULT_MAX_SUBSCRIBERS,
        max_queued_bytes: int = DEFAULT_MAX_QUEUED_BYTES,
    ) -> None:
        self._subs: list[asyncio.Queue[Any]] = []
        self._history: list[Event] = []
        self._max_history = max_history
        self._queue_maxsize = queue_maxsize
        self._max_subscribers = max_subscribers
        self._max_queued_bytes = max_queued_bytes
        self._queued_bytes = 0
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopped = False
        self._published = 0
        self._rejected = 0
        self._dropped = 0
        self._last_reject_reason: str | None = None

    @property
    def last_reject_reason(self) -> str | None:
        return self._last_reject_reason

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def stats(self) -> BusStats:
        with self._lock:
            return BusStats(
                published=self._published,
                rejected=self._rejected,
                dropped=self._dropped,
                subscribers=len(self._subs),
                queued_bytes=self._queued_bytes,
                stopped=self._stopped,
            )

    def _payload_bytes(self, event: Event) -> int:
        try:
            return len(
                json.dumps(event.payload, default=str, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
        except (TypeError, ValueError):
            return 0

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
            with self._lock:
                self._rejected += 1
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
        """Must run on the bound event-loop thread when a loop is running."""
        with self._lock:
            if self._stopped:
                return
            size = self._payload_bytes(event)
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history :]
            self._published += 1
            for q in list(self._subs):
                if self._queued_bytes + size > self._max_queued_bytes:
                    self._dropped += 1
                    continue
                try:
                    q.put_nowait(event)
                    self._queued_bytes += size
                    # stash size on queue for reclaim on get (weak: average)
                    pending = getattr(q, "_winos_pending_sizes", None)
                    if pending is None:
                        pending = []
                        setattr(q, "_winos_pending_sizes", pending)
                    pending.append(size)
                except asyncio.QueueFull:
                    self._dropped += 1

    def _schedule_fan_out(self, event: Event) -> None:
        """Non-blocking thread→loop handoff when a running loop is bound."""
        with self._lock:
            if self._stopped:
                return
            loop = self._loop
        if loop is not None and loop.is_running():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not loop:
                # Foreign thread (or different loop): schedule, never block.
                loop.call_soon_threadsafe(self._fan_out, event)
                return
        self._fan_out(event)

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
        # Bind loop from async publish path too.
        with self._lock:
            self._loop = asyncio.get_running_loop()
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
        self._schedule_fan_out(accepted)
        return accepted

    async def subscribe(self) -> AsyncIterator[Event]:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._stopped:
                raise BusClosedError("event bus is stopped")
            if len(self._subs) >= self._max_subscribers:
                raise BusAtCapacityError(
                    f"max subscribers ({self._max_subscribers}) reached"
                )
            self._loop = loop
            q: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._queue_maxsize)
            setattr(q, "_winos_pending_sizes", [])
            self._subs.append(q)
        try:
            while True:
                ev = await q.get()
                pending: list[int] = getattr(q, "_winos_pending_sizes", [])
                if pending:
                    reclaimed = pending.pop(0)
                    with self._lock:
                        self._queued_bytes = max(0, self._queued_bytes - reclaimed)
                if ev is _STOP:
                    break
                yield ev  # type: ignore[misc]
        finally:
            with self._lock:
                if q in self._subs:
                    self._subs.remove(q)
                # Reclaim any leftover queued bytes for this subscriber.
                leftover = sum(getattr(q, "_winos_pending_sizes", []))
                self._queued_bytes = max(0, self._queued_bytes - leftover)
                setattr(q, "_winos_pending_sizes", [])

    def stop(self) -> None:
        """Signal all subscribers to exit; further publishes are no-ops."""
        with self._lock:
            self._stopped = True
            loop = self._loop
            # Real asyncio queues get a sentinel; stub entries (e.g. N002 leak
            # markers) are dropped immediately so reset cannot AttributeError.
            queues = [q for q in self._subs if hasattr(q, "put_nowait")]
            self._subs = list(queues)

        def _inject_stop() -> None:
            for q in queues:
                self._put_stop(q)

        if loop is not None and loop.is_running():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not loop:
                loop.call_soon_threadsafe(_inject_stop)
                return
        _inject_stop()

    def _put_stop(self, q: asyncio.Queue[Any]) -> None:
        try:
            q.put_nowait(_STOP)
            return
        except asyncio.QueueFull:
            pass
        # Make room for the sentinel so shutdown cannot stall.
        try:
            q.get_nowait()
            pending: list[int] = getattr(q, "_winos_pending_sizes", [])
            if pending:
                reclaimed = pending.pop(0)
                with self._lock:
                    self._queued_bytes = max(0, self._queued_bytes - reclaimed)
        except asyncio.QueueEmpty:
            pass
        try:
            q.put_nowait(_STOP)
        except asyncio.QueueFull:
            with self._lock:
                self._dropped += 1

    def history(self, limit: int = 50) -> list[Event]:
        with self._lock:
            return list(self._history[-limit:])


_bus: EventBus | None = None
_bus_guard = threading.Lock()


def get_event_bus() -> EventBus:
    global _bus
    with _bus_guard:
        if _bus is None:
            _bus = EventBus()
        return _bus


def reset_event_bus() -> None:
    """Stop any live bus and drop the process-wide singleton (test isolation)."""
    global _bus
    with _bus_guard:
        old = _bus
        _bus = None
    if old is not None:
        old.stop()
