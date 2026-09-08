"""systemd service list/control — user + system scopes."""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any, Callable

from windows_os_api.os.capability import DiscoveryFailed


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
    """Unit systemd, user e system.

    Non inventa righe. Restituiva due servizi che non esistono: uno chiamato
    "systemctl" quando systemctl mancava, e uno chiamato "none" quando non
    c'erano unit. Erano la stessa cosa del "WinOsApi" tolto in #27 — una riga
    su cui il chiamante agisce e sbaglia. Da quando esiste il contratto
    `supported` (#28) sono anche superflue: l'assenza di systemctl la riporta
    il flag `services`, e zero unit e' gia' dicibile con una lista vuota.
    """
    if not shutil.which("systemctl"):
        return []
    run = run or _default_run
    out: list[dict[str, Any]] = []
    scopes = (
        [("user", ["systemctl", "--user", "list-units", "--type=service", "--no-pager", "--plain"]),
         ("system", ["systemctl", "list-units", "--type=service", "--no-pager", "--plain"])]
        if prefer_user
        else [("system", ["systemctl", "list-units", "--type=service", "--no-pager", "--plain"]),
              ("user", ["systemctl", "--user", "list-units", "--type=service", "--no-pager", "--plain"])]
    )
    failures: list[str] = []
    for scope, args in scopes:
        try:
            r = run(args, timeout=10)
            parsed = parse_list_units(r.stdout or "", scope=scope)
            if r.returncode != 0 and not parsed:
                # Uno scope puo' fallire legittimamente — `--user` senza sessione,
                # `system` senza privilegi — quindi si annota e si prova l'altro.
                failures.append(f"{scope}: {(r.stderr or '').strip()[:200] or f'exit {r.returncode}'}")
                continue
            out.extend(parsed)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{scope}: {exc}")

    # Dedupe by name preferring user scope first occurrence
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for s in out:
        key = f"{s['name']}:{s.get('scope')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)

    if not deduped and failures:
        # Systemd non raggiungibile (tipico in container: il binario c'e', il bus
        # no) non e' «nessun servizio». Prima questa condizione produceva una
        # lista vuota indistinguibile da una macchina senza unit — cioe' l'API
        # affermava un fatto che non aveva verificato. Ora il contratto #28 la
        # riporta come DISCOVERY_FAILED.
        raise DiscoveryFailed(
            "systemctl non ha potuto elencare le unit (" + "; ".join(failures) + ")"
        )
    return deduped[:150]


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
