"""N008 security: restart still gated by empty D1 allowlist; no ADMIN bypass."""
from __future__ import annotations

import pytest

from windows_os_api.os.services import service as svc
from windows_os_api.os.services.allowlist import (
    ALLOWLIST_EMPTY,
    ENV_VAR,
    SERVICE_NOT_ALLOWED,
)


@pytest.fixture(autouse=True)
def clean_allowlist(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)


class _RecordingWindows:
    name = "windows"
    NOT_IMPLEMENTED = frozenset({"audio"})

    def __init__(self):
        self.calls = []

    def capability_flags(self):
        return {"services": True, "service_control": True}

    def control_service(self, name, action, **kw):
        self.calls.append((name, action))
        raise AssertionError(
            f"backend reached for {name!r}/{action!r}: allowlist must block first"
        )


@pytest.fixture
def recording(monkeypatch):
    backend = _RecordingWindows()
    monkeypatch.setattr(
        "windows_os_api.os.services.service.get_backend", lambda: backend
    )
    return backend


def test_empty_allowlist_blocks_restart(recording):
    out = svc.control("Spooler", "restart")
    assert out["ok"] is False
    assert out["denied"] is True
    assert out["code"] == ALLOWLIST_EMPTY
    assert recording.calls == []


def test_non_allowlisted_restart_blocked(recording, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "WinOsN008Test")
    out = svc.control("Spooler", "restart")
    assert out["denied"] is True
    assert out["code"] == SERVICE_NOT_ALLOWED
    assert recording.calls == []


def test_admin_cannot_bypass_for_restart(recording, monkeypatch):
    monkeypatch.setenv("WINOS_ALLOW_PRIVILEGED", "true")
    monkeypatch.setenv("WINOS_ADMIN", "true")
    out = svc.control("Spooler", "restart")
    assert out["denied"] is True
    assert recording.calls == []


def test_allowlisted_restart_reaches_backend(monkeypatch):
    calls = []

    class _Ok:
        name = "windows"
        NOT_IMPLEMENTED = frozenset({"audio"})

        def capability_flags(self):
            return {"service_control": True}

        def control_service(self, name, action, **kw):
            calls.append((name, action))
            return {
                "ok": True,
                "name": name,
                "action": action,
                "status": "running",
                "verified": True,
            }

    monkeypatch.setattr(
        "windows_os_api.os.services.service.get_backend", lambda: _Ok()
    )
    monkeypatch.setenv(ENV_VAR, "WinOsN008Test")
    out = svc.control("WinOsN008Test", "restart")
    assert out["ok"] is True, out
    assert calls == [("WinOsN008Test", "restart")]
