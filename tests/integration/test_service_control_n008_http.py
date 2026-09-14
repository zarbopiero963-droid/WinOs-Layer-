"""N008 HTTP mapping: 403 / 404 / 409 / 504 for distinct SCM failure codes."""
from __future__ import annotations

import pytest

from windows_os_api.os.services.allowlist import ENV_VAR


@pytest.fixture(autouse=True)
def clean_allowlist(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)


def test_allowlist_empty_restart_is_403(client, auth_headers):
    r = client.post(
        "/v1/services/WinOsN008Test",
        headers=auth_headers,
        json={"action": "restart"},
    )
    assert r.status_code == 403, r.text


def test_not_found_is_404(client, auth_headers, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "MissingSvc")

    class _B:
        name = "windows"
        NOT_IMPLEMENTED = frozenset({"audio"})

        def capability_flags(self):
            return {"service_control": True}

        def control_service(self, name, action, **kw):
            return {
                "ok": False,
                "name": name,
                "action": action,
                "code": "not_found",
                "error": "service missing",
            }

    monkeypatch.setattr(
        "windows_os_api.os.services.service.get_backend", lambda: _B()
    )
    r = client.post(
        "/v1/services/MissingSvc",
        headers=auth_headers,
        json={"action": "status"},
    )
    assert r.status_code == 404, r.text


def test_conflict_is_409(client, auth_headers, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "WinOsN008Test")

    class _B:
        name = "windows"
        NOT_IMPLEMENTED = frozenset({"audio"})

        def capability_flags(self):
            return {"service_control": True}

        def control_service(self, name, action, **kw):
            return {
                "ok": False,
                "name": name,
                "action": action,
                "code": "conflict",
                "error": "incompatible transition",
                "status": "stopping",
            }

    monkeypatch.setattr(
        "windows_os_api.os.services.service.get_backend", lambda: _B()
    )
    r = client.post(
        "/v1/services/WinOsN008Test",
        headers=auth_headers,
        json={"action": "start"},
    )
    assert r.status_code == 409, r.text


def test_timeout_is_504(client, auth_headers, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "WinOsN008Test")

    class _B:
        name = "windows"
        NOT_IMPLEMENTED = frozenset({"audio"})

        def capability_flags(self):
            return {"service_control": True}

        def control_service(self, name, action, **kw):
            return {
                "ok": False,
                "name": name,
                "action": action,
                "code": "timeout",
                "error": "still stopping",
                "status": "stopping",
            }

    monkeypatch.setattr(
        "windows_os_api.os.services.service.get_backend", lambda: _B()
    )
    r = client.post(
        "/v1/services/WinOsN008Test",
        headers=auth_headers,
        json={"action": "stop"},
    )
    assert r.status_code == 504, r.text


def test_operational_failure_returns_body_ok_false(client, auth_headers, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "WinOsN008Test")

    class _B:
        name = "windows"
        NOT_IMPLEMENTED = frozenset({"audio"})

        def capability_flags(self):
            return {"service_control": True}

        def control_service(self, name, action, **kw):
            return {
                "ok": False,
                "name": name,
                "action": action,
                "code": "start_failed",
                "error": "boom",
                "status": "stopped",
            }

    monkeypatch.setattr(
        "windows_os_api.os.services.service.get_backend", lambda: _B()
    )
    r = client.post(
        "/v1/services/WinOsN008Test",
        headers=auth_headers,
        json={"action": "start"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["code"] == "start_failed"
