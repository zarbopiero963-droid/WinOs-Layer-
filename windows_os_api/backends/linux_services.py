"""systemd service list/control — user + system scopes."""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any, Callable


RunFn = Callable[..., subprocess.CompletedProcess]


def _default_run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=kw.get("timeout", 10))  # noqa: S603


def parse_list_units(text: str, *, scope: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        unit = parts[0]
        if not unit.endswith(".service"):
            continue
        # UNIT LOAD ACTIVE SUB DESCRIPTION...
        out.append(
            {
                "name": unit.removesuffix(".service"),
                "unit": unit,
                "load": parts[1],
                "active": parts[2] if len(parts) > 2 else "",
                "sub": parts[3] if len(parts) > 3 else "",
                "status": parts[2] if len(parts) > 2 else "unknown",
                "scope": scope,
                "description": " ".join(parts[4:]) if len(parts) > 4 else "",
            }
        )
    return out


def list_services(run: RunFn | None = None, *, prefer_user: bool = True) -> list[dict[str, Any]]:
    if not shutil.which("systemctl"):
        return [
            {
                "name": "systemctl",
                "status": "unavailable",
                "note": "systemctl not found",
            }
        ]
    run = run or _default_run
    out: list[dict[str, Any]] = []
    scopes = (
        [("user", ["systemctl", "--user", "list-units", "--type=service", "--no-pager", "--plain"]),
         ("system", ["systemctl", "list-units", "--type=service", "--no-pager", "--plain"])]
        if prefer_user
        else [("system", ["systemctl", "list-units", "--type=service", "--no-pager", "--plain"]),
              ("user", ["systemctl", "--user", "list-units", "--type=service", "--no-pager", "--plain"])]
    )
    for scope, args in scopes:
        try:
            r = run(args, timeout=10)
            if r.returncode != 0 and scope == "system":
                # may need elevated rights — still try to parse partial
                pass
            parsed = parse_list_units(r.stdout or "", scope=scope)
            out.extend(parsed)
        except Exception:  # noqa: BLE001
            continue
    # Dedupe by name preferring user scope first occurrence
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for s in out:
        key = f"{s['name']}:{s.get('scope')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    return deduped[:150] if deduped else [
        {"name": "none", "status": "empty", "note": "no units listed"}
    ]


def control_service(
    name: str,
    action: str,
    *,
    scope: str = "user",
    run: RunFn | None = None,
) -> dict[str, Any]:
    if action not in ("start", "stop", "restart", "status", "enable", "disable"):
        return {"ok": False, "error": f"unknown action: {action}", "name": name}
    unit = name if name.endswith(".service") else f"{name}.service"
    # Sanitize unit name FIRST — block shell metacharacters / path traversal
    if not re.fullmatch(r"[A-Za-z0-9_@.:\-]+\.service", unit):
        return {
            "ok": False,
            "denied": True,
            "code": "invalid_unit",
            "error": "invalid service unit name",
            "name": name,
        }
    if not shutil.which("systemctl"):
        return {"ok": False, "error": "systemctl not installed", "name": name, "action": action}
    run = run or _default_run
    args = ["systemctl"]
    if scope == "user":
        args.append("--user")
    elif scope != "system":
        return {"ok": False, "error": f"unknown scope: {scope}", "name": name}
    args.extend([action, unit])
    try:
        r = run(args, timeout=15)
        result = {
            "ok": r.returncode == 0,
            "name": name,
            "action": action,
            "scope": scope,
            "stdout": (r.stdout or "")[:2000],
            "stderr": (r.stderr or "")[:1000],
            "exit_code": r.returncode,
        }
        err_l = (r.stderr or "").lower()
        if r.returncode != 0:
            if "access denied" in err_l or "interactive authentication" in err_l or "permission" in err_l:
                result["denied"] = True
                result["code"] = "permission_denied"
                result["error"] = "systemctl requires elevated permissions for this unit"
            else:
                result["error"] = (r.stderr or r.stdout or "systemctl failed")[:500]
        return result
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "name": name, "action": action, "scope": scope}
