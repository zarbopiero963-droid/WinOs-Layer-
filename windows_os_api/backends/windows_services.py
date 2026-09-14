"""Windows SCM service control via pywin32 — query/start/stop (N007 / H63-N007).

Allowlist and RBAC live above this module (`os/services/service.py` +
`allowlist.py` + `Permission.SERVICE_CONTROL`). By the time a call reaches here
the service name is already authorised; this module performs the SCM operation
and reports an honest outcome.

N007 scope: ``start``, ``stop``, ``status``. Restart / timeout / recovery waits
are N008 — those actions are refused here without touching the SCM for restart
as a stop+start shortcut (that would invent success semantics N008 owns).

Ambiguous transitional states (``starting`` / ``stopping``) are **not** success:
``ok`` is true only when the post-action query shows the terminal state the
caller asked for (running after start, stopped after stop).
"""
from __future__ import annotations

import re
from typing import Any

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

# Same vocabulary as WindowsBackend.list_services / Linux systemd mapping.
SERVICE_STATES = {
    SERVICE_STOPPED: "stopped",
    SERVICE_START_PENDING: "starting",
    SERVICE_STOP_PENDING: "stopping",
    SERVICE_RUNNING: "running",
    SERVICE_CONTINUE_PENDING: "continuing",
    SERVICE_PAUSE_PENDING: "pausing",
    SERVICE_PAUSED: "paused",
}

# Short service names as EnumServicesStatus reports them (e.g. Spooler).
_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./\\-]{0,255}$")

# N007 actions only. restart/enable/disable → N008 or explicit refusal.
N007_ACTIONS = frozenset({"start", "stop", "status"})

# Win32 errors that mean "already in the desired state" — not a failure if the
# subsequent QueryServiceStatus confirms the terminal state.
_ERROR_SERVICE_ALREADY_RUNNING = 1056
_ERROR_SERVICE_NOT_ACTIVE = 1062
_ERROR_SERVICE_DOES_NOT_EXIST = 1060
_ERROR_ACCESS_DENIED = 5


def _state_name(code: int) -> str:
    return SERVICE_STATES.get(int(code), f"state_{code}")


def _winerror(exc: BaseException) -> int | None:
    return getattr(exc, "winerror", None)


def control_service(
    name: str,
    action: str,
    *,
    win32service: Any | None,
) -> dict[str, Any]:
    """Start, stop, or query a Windows service through the SCM.

    ``win32service`` is the ``win32service`` module (injectable for tests).
    When it is missing the capability is unavailable on this machine — not
    ``CAPABILITY_NOT_SUPPORTED`` (the backend *does* implement control).
    """
    if action not in N007_ACTIONS:
        return {
            "ok": False,
            "name": name,
            "action": action,
            "code": "action_not_supported",
            "error": (
                f"action {action!r} is outside N007 (start/stop/status); "
                f"restart/timeout/recovery belong to N008"
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

    # Strip a trailing .service if a Linux-style name leaked through; SCM uses
    # the short name. Allowlist already canonicalises the same way.
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
                }
            return {
                "ok": False,
                "name": canonical,
                "action": action,
                "code": "open_failed",
                "error": str(exc),
            }

        if action == "start":
            try:
                svc.StartService(handle, None)
            except Exception as exc:  # noqa: BLE001
                if _winerror(exc) != _ERROR_SERVICE_ALREADY_RUNNING:
                    return {
                        "ok": False,
                        "name": canonical,
                        "action": action,
                        "code": "start_failed",
                        "error": str(exc),
                        "status": _query_status_name(svc, handle),
                    }
        elif action == "stop":
            try:
                control = getattr(svc, "SERVICE_CONTROL_STOP", SERVICE_CONTROL_STOP)
                svc.ControlService(handle, control)
            except Exception as exc:  # noqa: BLE001
                if _winerror(exc) != _ERROR_SERVICE_NOT_ACTIVE:
                    return {
                        "ok": False,
                        "name": canonical,
                        "action": action,
                        "code": "stop_failed",
                        "error": str(exc),
                        "status": _query_status_name(svc, handle),
                    }

        state_code, status_name = _query_status(svc, handle)
        if action == "status":
            return {
                "ok": True,
                "name": canonical,
                "action": action,
                "status": status_name,
                "state_code": state_code,
            }

        # Ambiguous transitional state is NOT success (N007 / scheda invariant).
        if action == "start":
            ok = state_code == SERVICE_RUNNING
            expected = "running"
        else:  # stop
            ok = state_code == SERVICE_STOPPED
            expected = "stopped"

        out: dict[str, Any] = {
            "ok": ok,
            "name": canonical,
            "action": action,
            "status": status_name,
            "state_code": state_code,
            "verified": True,
        }
        if not ok:
            out["code"] = "ambiguous_state"
            out["error"] = (
                f"after {action} the service is {status_name!r}, not {expected!r}; "
                f"transitional/ambiguous state is not success"
            )
        return out
    finally:
        _close(svc, handle)
        _close(svc, scm)


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
