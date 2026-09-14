"""In-memory sliding-window rate limiter + concurrency gate (N013)."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class RateLimiter:
    def __init__(self, limit_per_minute: int = 120) -> None:
        self.limit = limit_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = 60.0
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60.0:
                q.popleft()
            return max(0, self.limit - len(q))


class ConcurrencyGate:
    """Process-wide in-flight request cap (N013)."""

    def __init__(self, limit: int = 32) -> None:
        self.limit = max(1, int(limit))
        self._in_flight = 0
        self._lock = Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self._in_flight >= self.limit:
                return False
            self._in_flight += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._in_flight > 0:
                self._in_flight -= 1

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight
