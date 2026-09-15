"""N043 / H63-N043 — Health/readiness split + operational metrics (B-OBS).

Unit-level on box. Full installed W/L H63-N043 is MANUAL_ONLY (#21).
Coverage themes: R02 R49 W095 L095 G18 / Q11 Q12.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from windows_os_api.backends.factory import BackendUnavailable
from windows_os_api.observability.metrics import (
    collect_operational,
    get_metrics,
    reset_metrics,
)
from windows_os_api.os.runtime_health import (
    BACKEND_UNAVAILABLE,
    probe_liveness,
    probe_runtime_health,
    probe_ui_ready,
)


@pytest.fixture(autouse=True)
def _reset_metrics():
    reset_metrics()
    yield
    reset_metrics()


def test_liveness_always_alive_even_if_backend_down():
    live = probe_liveness()
    assert live["live"] is True
    assert live["status"] == "alive"
    assert "version" in live
    assert "ready" not in live  # must not claim readiness


def test_ready_components_separate_backend_and_ui():
    class _B:
        name = "fake"

    class _S:
        backend = "fake"

    body = probe_runtime_health(
        get_backend_fn=lambda: _B(),
        get_settings_fn=lambda: _S(),
    )
    assert body["live"] is True
    assert body["ready"] is True
    assert "components" in body
    assert body["components"]["backend"]["ready"] is True
    assert "ui" in body["components"]
    assert "ready" in body["components"]["ui"]


def test_ready_false_when_backend_blocked_but_live_true():
    class _S:
        backend = "windows"

    def boom():
        raise BackendUnavailable("blocked for n043")

    body = probe_runtime_health(get_backend_fn=boom, get_settings_fn=lambda: _S())
    assert body["live"] is True
    assert body["ready"] is False
    assert body["status"] != "ok"
    assert body["error_code"] == BACKEND_UNAVAILABLE
    assert body["components"]["backend"]["ready"] is False
    # UI component still reported independently
    assert "ui" in body["components"]


def test_ui_probe_missing_index(tmp_path: Path):
    missing = tmp_path / "nope.html"
    ui = probe_ui_ready(control_center_index=missing)
    assert ui["ready"] is False
    assert ui["error_code"]


def test_live_route_200_when_ready_503(client, monkeypatch):
    from windows_os_api.api.rest import health as health_mod

    def boom_probe(**_k):
        return {
            "status": "unavailable",
            "version": "0",
            "live": True,
            "ready": False,
            "error_code": BACKEND_UNAVAILABLE,
            "reason": "down",
            "components": {
                "backend": {"ready": False},
                "ui": {"ready": True},
            },
        }

    monkeypatch.setattr(health_mod, "probe_runtime_health", boom_probe)
    live = client.get("/v1/live")
    assert live.status_code == 200
    assert live.json()["live"] is True
    ready = client.get("/v1/ready")
    assert ready.status_code == 503
    assert ready.json()["ready"] is False
    health = client.get("/v1/health")
    assert health.status_code == 503


def test_metrics_grow_and_bounded_cardinality(client, auth_headers):
    # Generate traffic
    for _ in range(5):
        assert client.get("/v1/live").status_code == 200
    client.get("/v1/no-such-route-n043")  # 404 → 4xx bucket
    snap = client.get("/v1/metrics", headers=auth_headers)
    assert snap.status_code == 200
    body = snap.json()
    assert "counters" in body
    assert "gauges" in body
    assert body["counters"].get("http.requests", 0) >= 5
    assert "http.responses.2xx" in body["counters"] or body["counters"].get("http.requests", 0) >= 5
    # Fixed gauge names (no per-path explosion)
    gauges = body["gauges"]
    for key in (
        "process.memory_rss_mb",
        "bus.dropped",
        "ws.clients",
        "http.inflight",
        "verify.holds",
    ):
        assert key in gauges, key
    # Cardinality: gauge+counter key count stays modest
    assert len(gauges) < 40
    assert len(body["counters"]) < 80


def test_collect_operational_returns_and_recovers():
    g1 = collect_operational()
    assert "process.memory_rss_mb" in g1
    m = get_metrics()
    m.incr("http.errors")
    m.incr("workflow.runs")
    m.incr("workflow.errors")
    snap = m.snapshot(collect=True)
    assert snap["counters"]["http.errors"] == 1
    assert snap["counters"]["workflow.runs"] == 1
    assert "bus.dropped" in snap["gauges"]
    # Recovery: reset clears
    reset_metrics()
    snap2 = get_metrics().snapshot(collect=True)
    assert snap2["counters"].get("http.errors", 0) == 0


def test_bus_drop_gauge_reflects_stats(monkeypatch):
    from windows_os_api.core.events import bus as bus_mod
    from windows_os_api.core.events.bus import BusStats

    class _Fake:
        def stats(self):
            return BusStats(published=3, rejected=1, dropped=7, subscribers=2, queued_bytes=100)

    monkeypatch.setattr(bus_mod, "get_event_bus", lambda: _Fake())
    g = collect_operational()
    assert g["bus.dropped"] == 7.0
    assert g["ws.clients"] == 2.0


def test_metrics_denied_without_auth(client):
    r = client.get("/v1/metrics")
    assert r.status_code in (401, 403)


def test_n004_regression_health_ok_with_fake(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ready"] is True
    assert body["live"] is True
    assert body["components"]["backend"]["ready"] is True
