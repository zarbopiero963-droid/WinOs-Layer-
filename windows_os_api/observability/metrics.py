"""In-process metrics collector."""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._timings: dict[str, list[float]] = defaultdict(list)
        self._started = time.time()

    def incr(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counters[name] += n

    def timing(self, name: str, ms: float) -> None:
        with self._lock:
            self._timings[name].append(ms)
            if len(self._timings[name]) > 1000:
                self._timings[name] = self._timings[name][-500:]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            timings = {}
            for k, vals in self._timings.items():
                if vals:
                    timings[k] = {
                        "count": len(vals),
                        "avg_ms": sum(vals) / len(vals),
                        "max_ms": max(vals),
                    }
            return {
                "uptime_seconds": round(time.time() - self._started, 2),
                "counters": dict(self._counters),
                "timings": timings,
            }


_metrics: Metrics | None = None


def get_metrics() -> Metrics:
    global _metrics
    if _metrics is None:
        _metrics = Metrics()
    return _metrics
