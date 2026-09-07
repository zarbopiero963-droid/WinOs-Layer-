"""Privilege-aware elevation helpers — GATED, never silent root.

Privileged operations require:
  1. Permission.ADMIN on the AuthContext
  2. WINOS_ALLOW_PRIVILEGED=true (opt-in)

Elevation attempts use ``pkexec`` or ``sudo -n`` only when both gates pass.
Every attempt is audited. Hardware-protected / denied ops return structured
denials (never crash).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.audit import get_audit_logger


def privileged_allowed() -> bool:
    env = os.environ.get("WINOS_ALLOW_PRIVILEGED", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    try:
        from windows_os_api.core.runtime.config import get_settings

        return bool(get_settings().allow_privileged)
    except Exception:  # noqa: BLE001
        return False


def deny_structured(
    reason: str,
    *,
    code: str = "privilege_denied",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "denied": True,
        "code": code,
        "error": reason,
        "detail": detail or {},
    }


def require_admin_and_flag(
    auth_has_admin: bool,
    *,
    subject: str = "unknown",
    action: str = "privileged",
) -> dict[str, Any] | None:
    """Return a denial dict if gates fail; None if allowed to proceed."""
    audit = get_audit_logger()
    if not auth_has_admin:
        entry = deny_structured(
            "ADMIN permission required for privileged operation",
            code="missing_admin",
            detail={"action": action},
        )
        audit.log(
            "privilege.denied",
            subject=subject,
            resource=action,
            outcome="denied",
            detail=entry,
        )
        return entry
    if not privileged_allowed():
        entry = deny_structured(
            "Set WINOS_ALLOW_PRIVILEGED=true to enable elevation",
            code="flag_disabled",
            detail={"action": action, "env": "WINOS_ALLOW_PRIVILEGED"},
        )
        audit.log(
            "privilege.denied",
            subject=subject,
            resource=action,
            outcome="denied",
            detail=entry,
        )
        return entry
    return None


def attempt_elevation(
    argv: list[str],
    *,
    auth_has_admin: bool,
    subject: str = "unknown",
    action: str = "elevate",
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Run argv via pkexec or sudo -n when gates pass. Never silent root."""
    denied = require_admin_and_flag(auth_has_admin, subject=subject, action=action)
    if denied is not None:
        return denied

    audit = get_audit_logger()
    if not argv:
        return deny_structured("empty command", code="bad_request")

    runners: list[list[str]] = []
    if shutil.which("pkexec"):
        runners.append(["pkexec", *argv])
    if shutil.which("sudo"):
        runners.append(["sudo", "-n", *argv])

    if not runners:
        entry = deny_structured(
            "neither pkexec nor sudo available",
            code="no_elevator",
            detail={"argv": argv[:8]},
        )
        audit.log(
            "privilege.elevate",
            subject=subject,
            resource=action,
            outcome="denied",
            detail=entry,
        )
        return entry

    last_err = ""
    for cmd in runners:
        try:
            audit.log(
                "privilege.elevate.attempt",
                subject=subject,
                resource=action,
                outcome="attempt",
                detail={"cmd0": cmd[0], "argv": argv[:12]},
            )
            r = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            ok = r.returncode == 0
            result = {
                "ok": ok,
                "denied": not ok,
                "elevator": cmd[0],
                "exit_code": r.returncode,
                "stdout": (r.stdout or "")[:4000],
                "stderr": (r.stderr or "")[:2000],
                "argv": argv,
            }
            if not ok:
                # Structured denial for auth failures / hardware protection
                err_l = (r.stderr or "").lower()
                if "polkit" in err_l or "not authorized" in err_l or "a password is required" in err_l:
                    result["code"] = "elevation_auth_failed"
                    result["error"] = "elevation requires interactive authorization"
                elif "operation not permitted" in err_l or "hardware" in err_l:
                    result["code"] = "hardware_protected"
                    result["error"] = "operation blocked by OS / hardware protection"
                else:
                    result["code"] = "elevate_failed"
                    result["error"] = (r.stderr or r.stdout or "elevation failed")[:500]
            audit.log(
                "privilege.elevate",
                subject=subject,
                resource=action,
                outcome="success" if ok else "denied",
                detail={"elevator": cmd[0], "exit_code": r.returncode, "code": result.get("code")},
            )
            return result
        except subprocess.TimeoutExpired:
            last_err = "elevation timed out"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)

    entry = deny_structured(last_err or "elevation failed", code="elevate_failed")
    audit.log(
        "privilege.elevate",
        subject=subject,
        resource=action,
        outcome="denied",
        detail=entry,
    )
    return entry


def parse_loginctl_sessions(text: str) -> list[dict[str, Any]]:
    """Parse ``loginctl list-sessions --no-legend`` style output."""
    sessions: list[dict[str, Any]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        # SESSION UID USER SEAT TTY
        sid, uid, user = parts[0], parts[1], parts[2]
        seat = parts[3] if len(parts) > 3 else ""
        tty = parts[4] if len(parts) > 4 else ""
        if not sid.isdigit() and not sid.replace("-", "").isalnum():
            # skip headers
            if sid.lower() in ("session", "sessionsession"):
                continue
        sessions.append(
            {
                "id": sid,
                "uid": uid,
                "user": user,
                "seat": seat,
                "tty": tty,
                "state": "Active",
                "client": tty or seat or "local",
                "source": "loginctl",
            }
        )
    return sessions
