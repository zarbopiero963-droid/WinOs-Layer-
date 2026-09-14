"""N004 — honest health/readiness + D3 envelopes on bare list surfaces.

RED→GREEN against main@d6668ce findings (Phase 0 comment 5668178895):

1. GET /health must not claim status ok when get_backend() raises BackendUnavailable.
2. Corrupt/invalid config must not claim ok (CONFIG_INVALID).
3. Recovery: after BackendUnavailable, a working backend yields status ok again.
4. list_users / list_displays / list_processes / list_drives must return D3 envelopes
   (supported + data key), same pattern as printers/devices/services.

Does NOT claim installed W/L (#21) PASS or MANUAL_ONLY PASS.
"""
from __future__ import annotations

import json

import pytest

from windows_os_api.backends.factory import BackendUnavailable
from windows_os_api.os.capability import (
    CAPABILITY_NOT_SUPPORTED,
    CAPABILITY_UNAVAILABLE,
    DiscoveryFailed,
)
from windows_os_api.os.runtime_health import (
    BACKEND_UNAVAILABLE,
    CONFIG_INVALID,
    probe_runtime_health,
)


# ---------------------------------------------------------------------------
# Health honesty
# ---------------------------------------------------------------------------
def test_health_payload_ok_when_backend_available():
    class _B:
        name = "fake"

    class _S:
        backend = "fake"

    body = probe_runtime_health(
        get_backend_fn=lambda: _B(),
        get_settings_fn=lambda: _S(),
    )
    assert body["status"] == "ok"
    assert body["ready"] is True
    assert body["backend"] == "fake"
    assert "version" in body
    assert "error_code" not in body


def test_health_payload_not_ok_when_backend_unavailable():
    """Reproduce Phase 0 false-success: BackendUnavailable must not yield status ok."""

    class _S:
        backend = "windows"

    def boom():
        raise BackendUnavailable(
            "WindowsBackend requires win32. "
            "Use WINOS_BACKEND=linux (or auto) on Linux, "
            "WINOS_BACKEND=fake for CRM fixtures, "
            "or set WINOS_ALLOW_FAKE_FALLBACK=true."
        )

    body = probe_runtime_health(get_backend_fn=boom, get_settings_fn=lambda: _S())
    assert body["status"] != "ok", body
    assert body["ready"] is False
    assert body["error_code"] == BACKEND_UNAVAILABLE
    assert "version" in body
    assert "win32" in body.get("reason", "").lower() or "WindowsBackend" in body.get(
        "reason", ""
    )


def test_health_payload_not_ok_on_corrupt_config():
    def bad_settings():
        raise ValueError("invalid WINOS_BACKEND literal 'bogus'")

    body = probe_runtime_health(
        get_backend_fn=lambda: (_ for _ in ()).throw(RuntimeError("should not run")),
        get_settings_fn=bad_settings,
    )
    assert body["status"] != "ok", body
    assert body["ready"] is False
    assert body["error_code"] == CONFIG_INVALID
    assert "bogus" in body["reason"] or "invalid" in body["reason"].lower()


def test_health_recovery_after_unavailable():
    """Unavailable → fixed backend: envelope returns to ok (H63-N004 recovery)."""

    class _S:
        backend = "fake"

    class _B:
        name = "linux"

    state = {"fail": True}

    def get_backend():
        if state["fail"]:
            raise BackendUnavailable("temporarily unavailable")
        return _B()

    bad = probe_runtime_health(get_backend_fn=get_backend, get_settings_fn=lambda: _S())
    assert bad["ready"] is False
    assert bad["error_code"] == BACKEND_UNAVAILABLE

    state["fail"] = False
    good = probe_runtime_health(get_backend_fn=get_backend, get_settings_fn=lambda: _S())
    assert good["status"] == "ok"
    assert good["ready"] is True
    assert good["backend"] == "linux"
    assert "error_code" not in good


def test_health_http_503_when_backend_unavailable(monkeypatch):
    from windows_os_api.api.rest import health as health_mod
    from fastapi.responses import JSONResponse

    def boom_probe(**_kwargs):
        return {
            "status": "unavailable",
            "version": "1.0.0",
            "ready": False,
            "error_code": BACKEND_UNAVAILABLE,
            "reason": "backend missing",
        }

    monkeypatch.setattr(health_mod, "probe_runtime_health", boom_probe)
    result = health_mod.health()
    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
    payload = json.loads(result.body.decode())
    assert payload["status"] != "ok"
    assert payload["ready"] is False


def test_ready_mirrors_health_backend_probe(monkeypatch):
    from windows_os_api.api.rest import health as health_mod
    from fastapi.responses import JSONResponse

    def boom_probe(**_kwargs):
        return {
            "status": "unavailable",
            "version": "1.0.0",
            "ready": False,
            "error_code": BACKEND_UNAVAILABLE,
            "reason": "no backend",
        }

    monkeypatch.setattr(health_mod, "probe_runtime_health", boom_probe)
    result = health_mod.ready()
    assert isinstance(result, JSONResponse)
    assert result.status_code == 503


def test_health_route_ok_with_fake_client(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ready"] is True
    assert body.get("backend") == "fake"
    assert "version" in body


def test_factory_windows_on_linux_raises_without_fake_fallback(monkeypatch, tmp_path):
    """Env repro from Phase 0: WINOS_BACKEND=windows + no fake fallback."""
    import sys

    if sys.platform == "win32":
        pytest.skip("on Windows")
    monkeypatch.setenv("WINOS_BACKEND", "windows")
    monkeypatch.setenv("WINOS_ALLOW_FAKE_FALLBACK", "false")
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(tmp_path))
    from windows_os_api.core.runtime.config import get_settings
    from windows_os_api.backends.factory import reset_backend, get_backend

    get_settings.cache_clear()
    reset_backend()
    with pytest.raises(BackendUnavailable):
        get_backend()


# ---------------------------------------------------------------------------
# D3 envelopes on the four bare lists
# ---------------------------------------------------------------------------
class _StubBackend:
    name = "stub"

    def __init__(
        self,
        flags=None,
        not_implemented=(),
        users=None,
        displays=None,
        processes=None,
        drives=None,
        fail=None,
    ):
        self._flags = flags if flags is not None else {}
        self.NOT_IMPLEMENTED = frozenset(not_implemented)
        self._users = users if users is not None else [{"name": "alice"}]
        self._displays = displays if displays is not None else [{"id": 0}]
        self._processes = processes if processes is not None else [{"pid": 1}]
        self._drives = drives if drives is not None else [{"letter": "C:"}]
        self._fail = fail

    def capability_flags(self):
        return dict(self._flags)

    def list_users(self):
        if self._fail == "users":
            raise DiscoveryFailed("users probe failed")
        return list(self._users)

    def list_displays(self):
        if self._fail == "displays":
            raise DiscoveryFailed("displays probe failed")
        return list(self._displays)

    def list_processes(self):
        if self._fail == "processes":
            raise DiscoveryFailed("processes probe failed")
        return list(self._processes)

    def list_drives(self):
        if self._fail == "drives":
            raise DiscoveryFailed("drives probe failed")
        return list(self._drives)


@pytest.fixture()
def patch_backend(monkeypatch):
    def _apply(backend):
        monkeypatch.setattr(
            "windows_os_api.os.users.service.get_backend", lambda: backend
        )
        monkeypatch.setattr(
            "windows_os_api.os.display.service.get_backend", lambda: backend
        )
        monkeypatch.setattr(
            "windows_os_api.os.processes.service.get_backend", lambda: backend
        )
        monkeypatch.setattr(
            "windows_os_api.os.storage.service.get_backend", lambda: backend
        )
        return backend

    return _apply


def test_list_users_returns_d3_envelope(patch_backend):
    from windows_os_api.os.users import service as users

    patch_backend(_StubBackend(users=[{"name": "bob"}]))
    out = users.list_users()
    assert isinstance(out, dict)
    assert out["supported"] is True
    assert out["users"] == [{"name": "bob"}]
    assert "error_code" not in out


def test_list_displays_returns_d3_envelope(patch_backend):
    from windows_os_api.os.display import service as display

    patch_backend(_StubBackend(displays=[{"id": 1, "name": "Main"}]))
    out = display.list_displays()
    assert out["supported"] is True
    assert out["displays"] == [{"id": 1, "name": "Main"}]
    assert "error_code" not in out


def test_list_processes_returns_d3_envelope(patch_backend):
    from windows_os_api.os.processes import service as procs

    patch_backend(_StubBackend(processes=[{"pid": 42}]))
    out = procs.list_processes()
    assert out["supported"] is True
    assert out["processes"] == [{"pid": 42}]
    assert "error_code" not in out


def test_list_drives_returns_d3_envelope(patch_backend):
    from windows_os_api.os.storage import service as storage

    patch_backend(_StubBackend(drives=[{"letter": "D:"}]))
    out = storage.list_drives()
    assert out["supported"] is True
    assert out["drives"] == [{"letter": "D:"}]
    assert "error_code" not in out


def test_list_processes_unavailable_when_flag_false(patch_backend):
    from windows_os_api.os.processes import service as procs

    patch_backend(_StubBackend(flags={"processes": False}, not_implemented=()))
    out = procs.list_processes()
    assert out["supported"] is False
    assert out["processes"] == []
    assert out["error_code"] == CAPABILITY_UNAVAILABLE


def test_list_processes_not_supported_when_never_implemented(patch_backend):
    from windows_os_api.os.processes import service as procs

    patch_backend(
        _StubBackend(flags={"processes": False}, not_implemented={"processes"})
    )
    out = procs.list_processes()
    assert out["supported"] is False
    assert out["error_code"] == CAPABILITY_NOT_SUPPORTED


@pytest.mark.parametrize(
    "attr,flag,key,fail_key",
    [
        ("users", "users", "users", "users"),
        ("display", "displays", "displays", "displays"),
        ("processes", "processes", "processes", "processes"),
        ("storage", "drives", "drives", "drives"),
    ],
)
def test_discovery_failed_is_distinct_from_empty(patch_backend, attr, flag, key, fail_key):
    from windows_os_api.os import users, display, processes, storage

    fn = {
        "users": users.service.list_users,
        "display": display.service.list_displays,
        "processes": processes.service.list_processes,
        "storage": storage.service.list_drives,
    }[attr]

    patch_backend(_StubBackend(flags={flag: True}, fail=fail_key))
    out = fn()
    assert out["supported"] is True
    assert out[key] == []
    assert out["error_code"] == "DISCOVERY_FAILED"


def test_empty_supported_list_has_no_error_code(patch_backend):
    from windows_os_api.os.users import service as users

    patch_backend(_StubBackend(users=[]))
    out = users.list_users()
    assert out["supported"] is True
    assert out["users"] == []
    assert "error_code" not in out


def test_rest_routes_expose_supported_on_four_lists(client, auth_headers):
    for path, key in (
        ("/v1/processes", "processes"),
        ("/v1/users", "users"),
        ("/v1/displays", "displays"),
        ("/v1/storage/drives", "drives"),
    ):
        body = client.get(path, headers=auth_headers).json()
        assert body.get("supported") is True, (path, body)
        assert isinstance(body[key], list), (path, body)
        assert "error_code" not in body, (path, body)
        assert len(body[key]) >= 1, (path, body)
