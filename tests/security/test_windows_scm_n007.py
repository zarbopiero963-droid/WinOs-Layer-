"""N007 security: empty allowlist + access denied must mutate nothing on Windows SCM.

D1-B default-deny stays empty. Capability may be True (N007) but non-allowlisted
names never reach OpenService/StartService/ControlService.
"""
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
    """Backend that claims service_control and records SCM-bound calls."""

    name = "windows"
    NOT_IMPLEMENTED = frozenset({"audio"})
    calls: list

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


def test_empty_allowlist_never_reaches_windows_scm(recording):
    for action in ("start", "stop", "status"):
        out = svc.control("Spooler", action)
        assert out["ok"] is False, out
        assert out["denied"] is True, out
        assert out["code"] == ALLOWLIST_EMPTY, out
    assert recording.calls == []


def test_non_allowlisted_never_reaches_windows_scm(recording, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "WinOsN007Test")
    out = svc.control("Spooler", "stop")
    assert out["denied"] is True
    assert out["code"] == SERVICE_NOT_ALLOWED
    assert recording.calls == []


def test_admin_is_not_a_shortcut(recording, monkeypatch):
    monkeypatch.setenv("WINOS_ALLOW_PRIVILEGED", "true")
    monkeypatch.setenv("WINOS_ADMIN", "true")
    out = svc.control("Spooler", "stop")
    assert out["denied"] is True
    assert recording.calls == []


def test_allowlisted_name_does_reach_backend(monkeypatch):
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
    monkeypatch.setenv(ENV_VAR, "WinOsN007Test")
    out = svc.control("WinOsN007Test", "status")
    assert out["ok"] is True, out
    assert calls == [("WinOsN007Test", "status")]


def test_default_allowlist_remains_empty():
    from windows_os_api.os.services.allowlist import allowed_services

    assert allowed_services() == frozenset()
