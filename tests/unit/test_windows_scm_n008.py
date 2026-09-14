"""N008 / H63-N008 — SCM restart, limited waits, conflict, timeout, recovery.

Mocks only. Never touches a real SCM or CI runner system services.
"""
from __future__ import annotations

import pytest

from windows_os_api.backends import windows_services as wsvc


class _FakeError(Exception):
    def __init__(self, winerror: int, message: str = "boom"):
        super().__init__(message)
        self.winerror = winerror


class _FakeSCM:
    SC_MANAGER_CONNECT = wsvc.SC_MANAGER_CONNECT
    SERVICE_CONTROL_STOP = wsvc.SERVICE_CONTROL_STOP

    def __init__(
        self,
        *,
        state: int = wsvc.SERVICE_RUNNING,
        open_error=None,
        start_error=None,
        stop_error=None,
        start_states=None,
        stop_states=None,
    ):
        self._state = state
        self.open_error = open_error
        self.start_error = start_error
        self.stop_error = stop_error
        self._start_states = list(start_states or [])
        self._stop_states = list(stop_states or [])
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
        self.started.append(handle)  # attempt recorded even if it fails
        if self.start_error is not None:
            raise self.start_error
        if self._start_states:
            self._state = self._start_states.pop(0)
        else:
            self._state = wsvc.SERVICE_RUNNING

    def ControlService(self, handle, control):
        self.stopped.append((handle, control))  # attempt recorded even if it fails
        if self.stop_error is not None:
            raise self.stop_error
        if self._stop_states:
            self._state = self._stop_states.pop(0)
        else:
            self._state = wsvc.SERVICE_STOPPED

    def QueryServiceStatus(self, handle):
        self.queries += 1
        # Advance scripted transitional sequences on each query after the action.
        if self._start_states and self._state == wsvc.SERVICE_START_PENDING:
            self._state = self._start_states.pop(0)
        elif self._stop_states and self._state == wsvc.SERVICE_STOP_PENDING:
            self._state = self._stop_states.pop(0)
        return (0x10, self._state, 0, 0, 0, 0, 0)

    def CloseServiceHandle(self, handle):
        self.closed.append(handle)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


def test_restart_stop_then_start_reaches_running():
    fake = _FakeSCM(state=wsvc.SERVICE_RUNNING)
    out = wsvc.control_service(
        "WinOsN008Test", "restart", win32service=fake, timeout_sec=1
    )
    assert out["ok"] is True, out
    assert out["status"] == "running"
    assert fake.stopped and fake.started
    assert "scm-handle" in fake.closed


def test_restart_from_stopped_only_starts():
    fake = _FakeSCM(state=wsvc.SERVICE_STOPPED)
    out = wsvc.control_service(
        "WinOsN008Test", "restart", win32service=fake, timeout_sec=1
    )
    assert out["ok"] is True, out
    assert fake.stopped == []
    assert fake.started


def test_start_wait_resolves_pending_to_running():
    """Delayed start that reaches running within timeout → success."""
    fake = _FakeSCM(state=wsvc.SERVICE_STOPPED)
    clock = _Clock()

    def start(handle, args):
        fake.started.append(handle)
        fake._state = wsvc.SERVICE_START_PENDING
        fake._start_states = [wsvc.SERVICE_RUNNING]

    fake.StartService = start  # type: ignore[method-assign]
    out = wsvc.control_service(
        "WinOsN008Test",
        "start",
        win32service=fake,
        timeout_sec=1,
        poll_interval_sec=0.05,
        sleep=clock.sleep,
        clock=clock,
    )
    assert out["ok"] is True, out
    assert out["status"] == "running"


def test_stop_wait_timeout_is_not_success():
    """Stop that stays STOP_PENDING past timeout → timeout, not ok."""
    fake = _FakeSCM(state=wsvc.SERVICE_RUNNING)
    clock = _Clock()

    def stop(handle, control):
        fake.stopped.append((handle, control))
        fake._state = wsvc.SERVICE_STOP_PENDING
        # never advance to STOPPED

    fake.ControlService = stop  # type: ignore[method-assign]
    out = wsvc.control_service(
        "WinOsN008Test",
        "stop",
        win32service=fake,
        timeout_sec=0.2,
        poll_interval_sec=0.05,
        sleep=clock.sleep,
        clock=clock,
    )
    assert out["ok"] is False, out
    assert out["code"] == "timeout"
    assert out["status"] == "stopping"
    assert "scm-handle" in fake.closed


def test_restart_aborts_when_stop_times_out():
    fake = _FakeSCM(state=wsvc.SERVICE_RUNNING)
    clock = _Clock()

    def stop(handle, control):
        fake.stopped.append((handle, control))
        fake._state = wsvc.SERVICE_STOP_PENDING

    fake.ControlService = stop  # type: ignore[method-assign]
    out = wsvc.control_service(
        "WinOsN008Test",
        "restart",
        win32service=fake,
        timeout_sec=0.15,
        poll_interval_sec=0.05,
        sleep=clock.sleep,
        clock=clock,
    )
    assert out["ok"] is False, out
    assert out["code"] == "timeout"
    assert out.get("phase") == "stop"
    assert fake.started == []  # must not invent start after failed stop
    assert "scm-handle" in fake.closed


def test_restart_start_failure_after_stop_reports_recovery_state():
    """Stop ok, start fails → not ok; status honest; handles closed; later start ok."""
    fake = _FakeSCM(state=wsvc.SERVICE_RUNNING, start_error=_FakeError(31, "start boom"))
    out = wsvc.control_service(
        "WinOsN008Test", "restart", win32service=fake, timeout_sec=1
    )
    assert out["ok"] is False, out
    assert out["code"] == "start_failed"
    assert out.get("phase") == "start"
    assert out.get("recovered_to") == "stopped"
    assert fake.stopped and fake.started
    assert "scm-handle" in fake.closed

    # Subsequent start succeeds (zero orphans — fresh open).
    fake2 = _FakeSCM(state=wsvc.SERVICE_STOPPED)
    out2 = wsvc.control_service(
        "WinOsN008Test", "start", win32service=fake2, timeout_sec=1
    )
    assert out2["ok"] is True, out2
    assert "scm-handle" in fake2.closed


def test_start_while_stopping_is_conflict():
    fake = _FakeSCM(state=wsvc.SERVICE_STOP_PENDING)
    out = wsvc.control_service(
        "WinOsN008Test", "start", win32service=fake, timeout_sec=0
    )
    assert out["ok"] is False
    assert out["code"] == "conflict"
    assert fake.started == []


def test_stop_while_starting_is_conflict():
    fake = _FakeSCM(state=wsvc.SERVICE_START_PENDING)
    out = wsvc.control_service(
        "WinOsN008Test", "stop", win32service=fake, timeout_sec=0
    )
    assert out["ok"] is False
    assert out["code"] == "conflict"
    assert fake.stopped == []


def test_restart_while_pending_is_conflict():
    fake = _FakeSCM(state=wsvc.SERVICE_START_PENDING)
    out = wsvc.control_service(
        "WinOsN008Test", "restart", win32service=fake, timeout_sec=0
    )
    assert out["ok"] is False
    assert out["code"] == "conflict"
    assert fake.started == []
    assert fake.stopped == []


def test_not_found_and_permission_codes_distinct():
    missing = _FakeSCM(open_error=_FakeError(wsvc._ERROR_SERVICE_DOES_NOT_EXIST))
    denied = _FakeSCM(open_error=_FakeError(wsvc._ERROR_ACCESS_DENIED))
    assert wsvc.control_service("No", "restart", win32service=missing)["code"] == "not_found"
    out = wsvc.control_service("No", "restart", win32service=denied)
    assert out["code"] == "permission_denied"
    assert out.get("denied") is True
