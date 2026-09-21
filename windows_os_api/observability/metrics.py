"""In-process metrics collector (N043 operational gauges).

Counters/timings stay fixed-name only — no per-user / per-path cardinality.
Gauges are overwritten on each ``collect_operational()`` / ``snapshot()``.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from typing import Any

# Hard caps so timing series cannot grow without bound.
_MAX_TIMING_SAMPLES = 1000
_TIMING_TRIM_TO = 500


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._timings: dict[str, list[float]] = defaultdict(list)
        self._gauges: dict[str, float] = {}
        self._started = time.time()

    def incr(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counters[name] += n

    def timing(self, name: str, ms: float) -> None:
        with self._lock:
            self._timings[name].append(ms)
            if len(self._timings[name]) > _MAX_TIMING_SAMPLES:
                self._timings[name] = self._timings[name][-_TIMING_TRIM_TO:]

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = float(value)

    def snapshot(self, *, collect: bool = True) -> dict[str, Any]:
        if collect:
            collect_operational(self)
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
                "gauges": dict(self._gauges),
            }


_metrics: Metrics | None = None
_metrics_lock = threading.Lock()


def get_metrics() -> Metrics:
    global _metrics
    with _metrics_lock:
        if _metrics is None:
            _metrics = Metrics()
        return _metrics


def reset_metrics() -> None:
    """Drop the process-wide metrics collector (counters/timings/gauges)."""
    global _metrics
    with _metrics_lock:
        _metrics = None


# Reused across samples: a fresh psutil.Process().cpu_percent(None) is always 0
# on the first call (audit H63-N043). Keep one Process for this PID.
_CPU_PROC = None
_CPU_PROC_PID: int | None = None


def _process_resource_gauges() -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        import psutil

        global _CPU_PROC, _CPU_PROC_PID
        pid = os.getpid()
        if _CPU_PROC is None or _CPU_PROC_PID != pid:
            _CPU_PROC = psutil.Process(pid)
            _CPU_PROC_PID = pid
            # Prime so the next sample is meaningful (first call is defined as 0.0).
            _CPU_PROC.cpu_percent(interval=None)
        proc = _CPU_PROC
        with proc.oneshot():
            out["process.cpu_percent"] = float(proc.cpu_percent(interval=None))
            mem = proc.memory_info()
            out["process.memory_rss_mb"] = round(mem.rss / (1024 * 1024), 3)
            out["process.memory_percent"] = float(proc.memory_percent())
            out["process.threads"] = float(proc.num_threads())
    except Exception:
        out.setdefault("process.cpu_percent", 0.0)
        out.setdefault("process.memory_rss_mb", 0.0)
        out.setdefault("process.memory_percent", 0.0)
    return out


def _bus_gauges() -> dict[str, float]:
    try:
        from windows_os_api.core.events.bus import get_event_bus

        st = get_event_bus().stats()
        return {
            "bus.published": float(st.published),
            "bus.rejected": float(st.rejected),
            "bus.dropped": float(st.dropped),
            "bus.subscribers": float(st.subscribers),
            "bus.queued_bytes": float(st.queued_bytes),
            "ws.clients": float(st.subscribers),  # WS fan-out == bus subscribers
        }
    except Exception:
        return {
            "bus.published": 0.0,
            "bus.rejected": 0.0,
            "bus.dropped": 0.0,
            "bus.subscribers": 0.0,
            "bus.queued_bytes": 0.0,
            "ws.clients": 0.0,
        }


def _inflight_gauge() -> dict[str, float]:
    try:
        from windows_os_api.api.rest.deps import get_concurrency_gate
        from windows_os_api.core.runtime.config import get_settings

        gate = get_concurrency_gate(get_settings())
        return {
            "http.inflight": float(gate.in_flight),
            "http.inflight_limit": float(gate.limit),
            # N046: senza questi due il budget per principal non e' osservabile —
            # si vedrebbe solo il totale, e un principal che satura la propria
            # quota mentre il totale e' basso sembrerebbe un sistema scarico.
            "http.inflight_principals": float(len(gate.principals())),
            "http.inflight_per_principal_limit": float(gate.per_principal_limit or 0),
        }
    except Exception:
        return {
            "http.inflight": 0.0,
            "http.inflight_limit": 0.0,
            "http.inflight_principals": 0.0,
            "http.inflight_per_principal_limit": 0.0,
        }


def _verify_hold_gauge() -> dict[str, float]:
    try:
        from windows_os_api.apps.adapters import verification as v

        return {"verify.holds": float(getattr(v, "verify_holds_count", lambda: 0)())}
    except Exception:
        return {"verify.holds": 0.0}


def collect_operational(metrics: Metrics | None = None) -> dict[str, float]:
    """Refresh fixed operational gauges (CPU/RAM/bus/ws/inflight/verify)."""
    m = metrics or get_metrics()
    gauges: dict[str, float] = {}
    gauges.update(_process_resource_gauges())
    gauges.update(_bus_gauges())
    gauges.update(_inflight_gauge())
    gauges.update(_verify_hold_gauge())
    for k, v in gauges.items():
        m.set_gauge(k, v)
    return gauges
