"""N050 / H63-N050 — system resources honesty + power owner gate (Q04).

Coverage refs: R02 R04 W006 L006 G01 G06.
Installed W/L: MANUAL_ONLY (never claim PASS here).

Contratto: inventario/uptime/resources senza valori inventati come successo;
power resta gated da R04/#64 (mai ok=True, anche sul fake).
"""
from __future__ import annotations

import os

import pytest

from windows_os_api.backends.factory import get_backend, reset_backend
from windows_os_api.core.runtime.config import get_settings
from windows_os_api.os.system import service as syssvc


@pytest.fixture
def fake_backend(monkeypatch):
    monkeypatch.setenv("WINOS_BACKEND", "fake")
    get_settings.cache_clear()
    reset_backend()
    yield get_backend()
    reset_backend()
    get_settings.cache_clear()


@pytest.mark.parametrize("action", ["shutdown", "reboot", "sleep", "hibernate", "lock"])
def test_power_never_ok_true_even_on_fake(fake_backend, action):
    """Falso successo Phase 0: fake tornava ok=True simulated."""
    out = syssvc.power(action)
    assert out.get("ok") is False
    assert out.get("denied") is True
    assert out.get("code") == "owner_decision_pending"
    assert out["detail"].get("decision") == "R04"
    assert out["detail"].get("issue") == 64
    # Defense in depth sul backend
    assert fake_backend.power_action(action).get("ok") is False


def test_fake_resources_declare_fixture(fake_backend):
    out = syssvc.resources()
    assert out["ok"] is True
    assert out["source"] == "fixture"
    assert out["fixture"] is True
    assert out["memory"]["total_mb"] > 0


def test_fake_system_info_and_uptime_declare_fixture(fake_backend):
    info = syssvc.system_info()
    assert info["ok"] is True and info["fixture"] is True
    assert info["source"] == "fixture"
    up = syssvc.uptime()
    assert up["ok"] is True and up["fixture"] is True


def test_linux_resources_match_proc_when_available():
    if os.name == "nt":
        pytest.skip("linux")
    prev_backend = os.environ.get("WINOS_BACKEND")
    prev_fallback = os.environ.get("WINOS_ALLOW_FAKE_FALLBACK")
    os.environ["WINOS_BACKEND"] = "linux"
    os.environ["WINOS_ALLOW_FAKE_FALLBACK"] = "false"
    get_settings.cache_clear()
    reset_backend()
    try:
        out = syssvc.resources()
        if out.get("ok") is False:
            assert out.get("code") == "RESOURCES_UNAVAILABLE"
            return
        assert out["ok"] is True
        assert out["source"] in {"psutil", "linux"}
        assert out["memory"]["total_mb"] > 100
        up = syssvc.uptime()
        assert up["ok"] is True
        assert up["uptime_seconds"] > 0
        # power still gated at service even if backend differs
        p = syssvc.power("shutdown")
        assert p["ok"] is False and p["code"] == "owner_decision_pending"
    finally:
        # Restore env — leaving WINOS_BACKEND=linux poisons later fake/UI tests
        # (workflows/heal expect Contoso fixture on fake backend).
        if prev_backend is None:
            os.environ.pop("WINOS_BACKEND", None)
        else:
            os.environ["WINOS_BACKEND"] = prev_backend
        if prev_fallback is None:
            os.environ.pop("WINOS_ALLOW_FAKE_FALLBACK", None)
        else:
            os.environ["WINOS_ALLOW_FAKE_FALLBACK"] = prev_fallback
        reset_backend()
        get_settings.cache_clear()


def test_unavailable_resources_are_not_idle_zeros(monkeypatch):
    """Shape psutil-missing: non deve diventare ok con cpu=0 inventato."""
    monkeypatch.setenv("WINOS_BACKEND", "fake")
    get_settings.cache_clear()
    reset_backend()
    # Monkeypatch backend method
    b = get_backend()

    def _bad():
        return {"cpu_percent": 0, "memory": {}, "disk": {}, "note": "psutil unavailable"}

    monkeypatch.setattr(b, "get_resources", _bad)
    out = syssvc.resources()
    assert out["ok"] is False
    assert out["code"] == "RESOURCES_UNAVAILABLE"
    assert "cpu_percent" not in out or out.get("ok") is False


def test_rest_power_returns_403(client, admin_headers):
    r = client.post("/v1/system/power/shutdown", headers=admin_headers)
    assert r.status_code == 403, r.text
    assert "owner_decision_pending" in r.text
