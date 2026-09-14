"""N007 / H63-N007 — Windows SCM start/stop/status via injectable win32service.

Never touches a real SCM. Mocks only. CI runner system services must not be
stopped by these tests.
"""
from __future__ import annotations

import inspect

import pytest

from windows_os_api.backends import windows_services as wsvc
from windows_os_api.os.capability import CAPABILITY_UNAVAILABLE


class _FakeError(Exception):
    def __init__(self, winerror: int, message: str = "boom"):
        super().__init__(message)
        self.winerror = winerror


class _FakeSCM:
    """Minimal stand-in for win32service used by control_service."""

    SC_MANAGER_CONNECT = wsvc.SC_MANAGER_CONNECT
    SERVICE_CONTROL_STOP = wsvc.SERVICE_CONTROL_STOP
    SERVICE_QUERY_STATUS = wsvc.SERVICE_QUERY_STATUS
    SERVICE_START = wsvc.SERVICE_START
    SERVICE_STOP = wsvc.SERVICE_STOP

    def __init__(
        self,
        *,
        state: int = wsvc.SERVICE_RUNNING,
        open_error=None,
        start_error=None,
        stop_error=None,
    ):
        self._state = state
        self.open_error = open_error
        self.start_error = start_error
        self.stop_error = stop_error
        self.opened: list = []
        self.started: list = []
        self.stopped: list = []
        self.queries = 0
        self.closed: list = []

    def OpenSCManager(self, machine, database, access):
        self.opened.append(("scm", access))
        return "scm-handle"

    def OpenService(self, scm, name, access):
        if self.open_error is not None:
            raise self.open_error
        self.opened.append(("svc", name, access))
        return f"svc:{name}"

    def StartService(self, handle, args):
        if self.start_error is not None:
            raise self.start_error
        self.started.append(handle)
        self._state = wsvc.SERVICE_RUNNING

    def ControlService(self, handle, control):
        if self.stop_error is not None:
            raise self.stop_error
        self.stopped.append((handle, control))
        self._state = wsvc.SERVICE_STOPPED

    def QueryServiceStatus(self, handle):
        self.queries += 1
        return (0x10, self._state, 0, 0, 0, 0, 0)

    def CloseServiceHandle(self, handle):
        self.closed.append(handle)


def test_status_queries_without_mutating():
    fake = _FakeSCM(state=wsvc.SERVICE_RUNNING)
    out = wsvc.control_service("WinOsN007Test", "status", win32service=fake)
    assert out["ok"] is True, out
    assert out["status"] == "running"
    assert fake.started == []
    assert fake.stopped == []
    assert fake.queries >= 1


def test_start_succeeds_only_when_running_afterward():
    fake = _FakeSCM(state=wsvc.SERVICE_STOPPED)

    def start(handle, args):
        fake.started.append(handle)
        fake._state = wsvc.SERVICE_RUNNING

    fake.StartService = start  # type: ignore[method-assign]
    out = wsvc.control_service("WinOsN007Test", "start", win32service=fake)
    assert out["ok"] is True, out
    assert out["status"] == "running"
    assert out["verified"] is True
    assert fake.started


def test_start_pending_is_not_success():
    """Ambiguous transitional state is NOT success (scheda invariant)."""
    fake = _FakeSCM(state=wsvc.SERVICE_STOPPED)

    def start(handle, args):
        fake.started.append(handle)
        fake._state = wsvc.SERVICE_START_PENDING

    fake.StartService = start  # type: ignore[method-assign]
    out = wsvc.control_service("WinOsN007Test", "start", win32service=fake, timeout_sec=0)
    assert out["ok"] is False, out
    assert out["code"] == "ambiguous_state"
    assert out["status"] == "starting"


def test_stop_pending_is_not_success():
    fake = _FakeSCM(state=wsvc.SERVICE_RUNNING)

    def stop(handle, control):
        fake.stopped.append((handle, control))
        fake._state = wsvc.SERVICE_STOP_PENDING

    fake.ControlService = stop  # type: ignore[method-assign]
    out = wsvc.control_service("WinOsN007Test", "stop", win32service=fake, timeout_sec=0)
    assert out["ok"] is False, out
    assert out["code"] == "ambiguous_state"
    assert out["status"] == "stopping"


def test_stop_reaches_stopped():
    fake = _FakeSCM(state=wsvc.SERVICE_RUNNING)
    out = wsvc.control_service("WinOsN007Test", "stop", win32service=fake)
    assert out["ok"] is True, out
    assert out["status"] == "stopped"
    assert fake.stopped


def test_already_running_is_success_when_query_confirms():
    fake = _FakeSCM(
        state=wsvc.SERVICE_RUNNING,
        start_error=_FakeError(wsvc._ERROR_SERVICE_ALREADY_RUNNING),
    )
    out = wsvc.control_service("WinOsN007Test", "start", win32service=fake)
    assert out["ok"] is True, out
    assert out["status"] == "running"


def test_access_denied_mutates_nothing():
    fake = _FakeSCM(open_error=_FakeError(wsvc._ERROR_ACCESS_DENIED, "access denied"))
    out = wsvc.control_service("WinOsN007Test", "stop", win32service=fake)
    assert out["ok"] is False, out
    assert out["code"] == "permission_denied"
    assert fake.started == []
    assert fake.stopped == []


def test_missing_service_is_not_found():
    fake = _FakeSCM(open_error=_FakeError(wsvc._ERROR_SERVICE_DOES_NOT_EXIST, "missing"))
    out = wsvc.control_service("NoSuchSvc", "status", win32service=fake)
    assert out["ok"] is False
    assert out["code"] == "not_found"


def test_invalid_name_never_opens_scm():
    fake = _FakeSCM()
    out = wsvc.control_service("evil;rm", "stop", win32service=fake)
    assert out["ok"] is False
    assert out["code"] == "invalid_service"
    assert fake.opened == []


def test_enable_is_still_refused_without_scm_call():
    """enable/disable remain out of SCM control scope (not N008 restart)."""
    fake = _FakeSCM()
    out = wsvc.control_service("WinOsN007Test", "enable", win32service=fake)
    assert out["ok"] is False
    assert out["code"] == "action_not_supported"
    assert fake.opened == []


def test_missing_win32service_is_unavailable_not_unsupported():
    out = wsvc.control_service("WinOsN007Test", "status", win32service=None)
    assert out["ok"] is False
    assert out["supported"] is False
    assert out["error_code"] == CAPABILITY_UNAVAILABLE


def test_windows_backend_declares_service_control_when_module_present():
    from windows_os_api.backends.windows import WindowsBackend, _WINDOWS_NOT_IMPLEMENTED

    assert "service_control" not in _WINDOWS_NOT_IMPLEMENTED
    source = inspect.getsource(WindowsBackend._probe_capabilities)
    assert '"service_control": self._win32service is not None' in source


def test_scm_handle_closed_after_open_failure():
    fake = _FakeSCM(open_error=_FakeError(5, "denied"))
    wsvc.control_service("WinOsN007Test", "stop", win32service=fake)
    assert "scm-handle" in fake.closed


def test_handles_closed_on_success():
    fake = _FakeSCM(state=wsvc.SERVICE_RUNNING)
    wsvc.control_service("WinOsN007Test", "status", win32service=fake)
    assert "scm-handle" in fake.closed
    assert any(str(h).startswith("svc:") for h in fake.closed)
