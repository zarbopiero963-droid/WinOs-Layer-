"""Windows SCM service control via pywin32 — query/start/stop/restart (N007+N008).

Allowlist and RBAC live above this module (`os/services/service.py` +
`allowlist.py` + `Permission.SERVICE_CONTROL`). By the time a call reaches here
the service name is already authorised; this module performs the SCM operation
and reports an honest outcome.

N007: ``start``, ``stop``, ``status`` with a single post-action query.
N008: ``restart``, limited waits for terminal state, incompatible-transition
refusal, distinct operational codes, and handle cleanup (no orphan handles).

Ambiguous transitional states (``starting`` / ``stopping``) are **not** success:
``ok`` is true only when the post-action (or post-wait) query shows the terminal
state the caller asked for. A wait that expires while still transitional yields
``code=timeout``, not success.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

from windows_os_api.os.capability import CAPABILITY_UNAVAILABLE

# winsvc.h / win32service values — duplicated so unit tests do not need pywin32.
SERVICE_STOPPED = 1
SERVICE_START_PENDING = 2
SERVICE_STOP_PENDING = 3
SERVICE_RUNNING = 4
SERVICE_CONTINUE_PENDING = 5
SERVICE_PAUSE_PENDING = 6
SERVICE_PAUSED = 7

SERVICE_CONTROL_STOP = 0x00000001

SERVICE_QUERY_STATUS = 0x0004
SERVICE_START = 0x0010
SERVICE_STOP = 0x0020

SC_MANAGER_CONNECT = 0x0001

SERVICE_STATES = {
    SERVICE_STOPPED: "stopped",
    SERVICE_START_PENDING: "starting",
    SERVICE_STOP_PENDING: "stopping",
    SERVICE_RUNNING: "running",
    SERVICE_CONTINUE_PENDING: "continuing",
    SERVICE_PAUSE_PENDING: "pausing",
    SERVICE_PAUSED: "paused",
}

_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./\\-]{0,255}$")

CONTROL_ACTIONS = frozenset({"start", "stop", "status", "restart"})

_START_BLOCKED_BY = frozenset({SERVICE_STOP_PENDING, SERVICE_PAUSE_PENDING})
_STOP_BLOCKED_BY = frozenset({SERVICE_START_PENDING, SERVICE_CONTINUE_PENDING})

_ERROR_SERVICE_ALREADY_RUNNING = 1056
_ERROR_SERVICE_NOT_ACTIVE = 1062
_ERROR_SERVICE_DOES_NOT_EXIST = 1060
_ERROR_ACCESS_DENIED = 5

DEFAULT_TIMEOUT_SEC = 30.0
DEFAULT_POLL_INTERVAL_SEC = 0.25

SleepFn = Callable[[float], None]
ClockFn = Callable[[], float]


def _state_name(code: int) -> str:
    return SERVICE_STATES.get(int(code), f"state_{code}")


def _winerror(exc: BaseException) -> int | None:
    return getattr(exc, "winerror", None)


def control_service(
    name: str,
    action: str,
    *,
    win32service: Any | None,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
    sleep: SleepFn | None = None,
    clock: ClockFn | None = None,
) -> dict[str, Any]:
    """Start, stop, restart, or query a Windows service through the SCM.

    ``timeout_sec <= 0`` disables waiting (single post-action query — N007
    immediate semantics). Positive values poll until the terminal state or
    timeout (N008).
    """
    sleep = sleep or time.sleep
    clock = clock or time.monotonic

    if action not in CONTROL_ACTIONS:
        return {
            "ok": False,
            "name": name,
            "action": action,
            "code": "action_not_supported",
            "error": (
                f"action {action!r} is outside SCM control "
                f"(start/stop/status/restart); enable/disable are out of scope"
            ),
        }

    if not isinstance(name, str) or not name.strip():
        return {
            "ok": False,
            "name": name,
            "action": action,
            "code": "invalid_service",
            "error": "invalid service name",
            "denied": True,
        }

    canonical = name.strip().removesuffix(".service")
    if not _VALID_NAME.fullmatch(canonical):
        return {
            "ok": False,
            "name": name,
            "action": action,
            "code": "invalid_service",
            "error": "invalid service name",
            "denied": True,
        }

    if win32service is None:
        return {
            "ok": False,
            "supported": False,
            "error_code": CAPABILITY_UNAVAILABLE,
            "error": (
                "service control is implemented on this backend, but win32service "
                "is not available on this machine"
            ),
            "name": canonical,
            "action": action,
        }

    svc = win32service
    access = SERVICE_QUERY_STATUS
    if action == "start":
        access |= SERVICE_START
    elif action == "stop":
        access |= SERVICE_STOP
    elif action == "restart":
        access |= SERVICE_START | SERVICE_STOP

    scm = None
    handle = None
    try:
        try:
            scm = svc.OpenSCManager(
                None, None, getattr(svc, "SC_MANAGER_CONNECT", SC_MANAGER_CONNECT)
            )
            handle = svc.OpenService(scm, canonical, access)
        except Exception as exc:  # noqa: BLE001
            code = _winerror(exc)
            if code == _ERROR_SERVICE_DOES_NOT_EXIST:
                return {
                    "ok": False,
                    "name": canonical,
                    "action": action,
                    "code": "not_found",
                    "error": str(exc),
                }
            if code == _ERROR_ACCESS_DENIED:
                return {
                    "ok": False,
                    "name": canonical,
                    "action": action,
                    "code": "permission_denied",
                    "error": str(exc),
                    "denied": True,
                }
            return {
                "ok": False,
                "name": canonical,
                "action": action,
                "code": "open_failed",
                "error": str(exc),
            }

        if action == "status":
            state_code, status_name = _query_status(svc, handle)
            return {
                "ok": True,
                "name": canonical,
                "action": action,
                "status": status_name,
                "state_code": state_code,
            }

        if action == "restart":
            return _restart(
                svc,
                handle,
                canonical,
                timeout_sec=timeout_sec,
                poll_interval_sec=poll_interval_sec,
                sleep=sleep,
                clock=clock,
            )

        state_code, status_name = _query_status(svc, handle)
        conflict = _incompatible(action, state_code)
        if conflict is not None:
            return conflict | {
                "name": canonical,
                "action": action,
                "status": status_name,
                "state_code": state_code,
            }

        if action == "start":
            return _do_start(
                svc,
                handle,
                canonical,
                timeout_sec=timeout_sec,
                poll_interval_sec=poll_interval_sec,
                sleep=sleep,
                clock=clock,
            )
        return _do_stop(
            svc,
            handle,
            canonical,
            timeout_sec=timeout_sec,
            poll_interval_sec=poll_interval_sec,
            sleep=sleep,
            clock=clock,
        )
    finally:
        _close(svc, handle)
        _close(svc, scm)


def _incompatible(action: str, state_code: int) -> dict[str, Any] | None:
    if action == "start" and state_code in _START_BLOCKED_BY:
        return {
            "ok": False,
            "code": "conflict",
            "error": (
                f"cannot start while service is {_state_name(state_code)!r}; "
                f"incompatible transition"
            ),
        }
    if action == "stop" and state_code in _STOP_BLOCKED_BY:
        return {
            "ok": False,
            "code": "conflict",
            "error": (
                f"cannot stop while service is {_state_name(state_code)!r}; "
                f"incompatible transition"
            ),
        }
    return None


def _do_start(
    svc: Any,
    handle: Any,
    canonical: str,
    *,
    timeout_sec: float,
    poll_interval_sec: float,
    sleep: SleepFn,
    clock: ClockFn,
) -> dict[str, Any]:
    try:
        svc.StartService(handle, None)
    except Exception as exc:  # noqa: BLE001
        if _winerror(exc) != _ERROR_SERVICE_ALREADY_RUNNING:
            return {
                "ok": False,
                "name": canonical,
                "action": "start",
                "code": "start_failed",
                "error": str(exc),
                "status": _query_status_name(svc, handle),
            }
    return _wait_terminal(
        svc,
        handle,
        canonical,
        action="start",
        expected_code=SERVICE_RUNNING,
        expected_name="running",
        timeout_sec=timeout_sec,
        poll_interval_sec=poll_interval_sec,
        sleep=sleep,
        clock=clock,
    )


def _do_stop(
    svc: Any,
    handle: Any,
    canonical: str,
    *,
    timeout_sec: float,
    poll_interval_sec: float,
    sleep: SleepFn,
    clock: ClockFn,
) -> dict[str, Any]:
    try:
        control = getattr(svc, "SERVICE_CONTROL_STOP", SERVICE_CONTROL_STOP)
        svc.ControlService(handle, control)
    except Exception as exc:  # noqa: BLE001
        if _winerror(exc) != _ERROR_SERVICE_NOT_ACTIVE:
            return {
                "ok": False,
                "name": canonical,
                "action": "stop",
                "code": "stop_failed",
                "error": str(exc),
                "status": _query_status_name(svc, handle),
            }
    return _wait_terminal(
        svc,
        handle,
        canonical,
        action="stop",
        expected_code=SERVICE_STOPPED,
        expected_name="stopped",
        timeout_sec=timeout_sec,
        poll_interval_sec=poll_interval_sec,
        sleep=sleep,
        clock=clock,
    )


def _restart(
    svc: Any,
    handle: Any,
    canonical: str,
    *,
    timeout_sec: float,
    poll_interval_sec: float,
    sleep: SleepFn,
    clock: ClockFn,
) -> dict[str, Any]:
    """Stop (wait) then start (wait). Partial failure is never reported as ok."""
    state_code, status_name = _query_status(svc, handle)
    if state_code in (SERVICE_START_PENDING, SERVICE_STOP_PENDING):
        return {
            "ok": False,
            "name": canonical,
            "action": "restart",
            "code": "conflict",
            "error": (
                f"cannot restart while service is {status_name!r}; "
                f"incompatible transition"
            ),
            "status": status_name,
            "state_code": state_code,
        }

    if state_code != SERVICE_STOPPED:
        stop_out = _do_stop(
            svc,
            handle,
            canonical,
            timeout_sec=timeout_sec,
            poll_interval_sec=poll_interval_sec,
            sleep=sleep,
            clock=clock,
        )
        if not stop_out.get("ok"):
            return {
                "ok": False,
                "name": canonical,
                "action": "restart",
                "code": stop_out.get("code") or "stop_failed",
                "error": (
                    f"restart aborted: stop did not reach stopped "
                    f"({stop_out.get('error') or stop_out.get('status')})"
                ),
                "status": stop_out.get("status"),
                "state_code": stop_out.get("state_code"),
                "phase": "stop",
                "stop_result": stop_out,
            }

    start_out = _do_start(
        svc,
        handle,
        canonical,
        timeout_sec=timeout_sec,
        poll_interval_sec=poll_interval_sec,
        sleep=sleep,
        clock=clock,
    )
    if not start_out.get("ok"):
        return {
            "ok": False,
            "name": canonical,
            "action": "restart",
            "code": start_out.get("code") or "start_failed",
            "error": (
                f"restart stop ok but start failed "
                f"({start_out.get('error') or start_out.get('status')})"
            ),
            "status": start_out.get("status"),
            "state_code": start_out.get("state_code"),
            "phase": "start",
            "start_result": start_out,
            "recovered_to": "stopped",
        }

    return {
        "ok": True,
        "name": canonical,
        "action": "restart",
        "status": start_out.get("status", "running"),
        "state_code": start_out.get("state_code", SERVICE_RUNNING),
        "verified": True,
    }


def _wait_terminal(
    svc: Any,
    handle: Any,
    canonical: str,
    *,
    action: str,
    expected_code: int,
    expected_name: str,
    timeout_sec: float,
    poll_interval_sec: float,
    sleep: SleepFn,
    clock: ClockFn,
) -> dict[str, Any]:
    waited = False
    deadline = clock() + max(timeout_sec, 0.0)
    state_code, status_name = _query_status(svc, handle)

    if timeout_sec > 0 and state_code != expected_code:
        waited = True
        while clock() < deadline:
            if state_code == expected_code:
                break
            interval = min(poll_interval_sec, max(0.0, deadline - clock()))
            if interval > 0:
                sleep(interval)
            state_code, status_name = _query_status(svc, handle)
        else:
            state_code, status_name = _query_status(svc, handle)

    ok = state_code == expected_code
    out: dict[str, Any] = {
        "ok": ok,
        "name": canonical,
        "action": action,
        "status": status_name,
        "state_code": state_code,
        "verified": True,
    }
    if not ok:
        transitional = state_code in (
            SERVICE_START_PENDING,
            SERVICE_STOP_PENDING,
            SERVICE_CONTINUE_PENDING,
            SERVICE_PAUSE_PENDING,
        )
        if waited and transitional:
            out["code"] = "timeout"
            out["error"] = (
                f"after {action} wait the service is still {status_name!r}, "
                f"not {expected_name!r}; timeout is not success"
            )
        elif transitional:
            out["code"] = "ambiguous_state"
            out["error"] = (
                f"after {action} the service is {status_name!r}, not {expected_name!r}; "
                f"transitional/ambiguous state is not success"
            )
        else:
            out["code"] = f"{action}_failed"
            out["error"] = (
                f"after {action} the service is {status_name!r}, not {expected_name!r}"
            )
    return out


def _query_status(svc: Any, handle: Any) -> tuple[int, str]:
    status = svc.QueryServiceStatus(handle)
    state_code = int(status[1])
    return state_code, _state_name(state_code)


def _query_status_name(svc: Any, handle: Any) -> str | None:
    try:
        return _query_status(svc, handle)[1]
    except Exception:  # noqa: BLE001
        return None


def _close(svc: Any, handle: Any) -> None:
    if handle is None:
        return
    try:
        svc.CloseServiceHandle(handle)
    except Exception:  # noqa: BLE001
        pass


N007_ACTIONS = frozenset({"start", "stop", "status"})
