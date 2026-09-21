"""System info / resources / uptime / power / capabilities."""
from __future__ import annotations

from typing import Any

from windows_os_api.backends.factory import get_backend


def system_info() -> dict[str, Any]:
    """N050 — inventario host senza valori inventati come successo."""
    raw = dict(get_backend().get_system_info())
    return _honest_inventory(raw, kind="system_info")


def resources() -> dict[str, Any]:
    """N050 — CPU/mem/disk con ``ok``/``source``; unavailable ≠ zeri finti."""
    raw = dict(get_backend().get_resources())
    return _honest_resources(raw)


def uptime() -> dict[str, Any]:
    """N050 — uptime con sorgente esplicita."""
    raw = dict(get_backend().get_uptime())
    return _honest_uptime(raw)


def power(action: str) -> dict[str, Any]:
    """N050 — power subordinato a decisione owner R04 (#64).

    Phase 0: il backend fake rispondeva ``ok: True, simulated: True``. Quello
    e' un falso successo: un client puo' credere all'effetto. Finche' #64 non
    approva power su VM sacrificabile, il service rifiuta **sempre** — anche
    sul fake — con ``owner_decision_pending``. I backend restano deny-by-default
    (defense in depth); qui non si delega l'ok.
    """
    from windows_os_api.core.security.privilege import deny_structured

    act = (action or "").strip().lower()
    return deny_structured(
        "power actions gated by owner decision R04 (#64); not implemented",
        code="owner_decision_pending",
        detail={
            "action": act,
            "decision": "R04",
            "issue": 64,
            "backend": getattr(get_backend(), "name", "?"),
        },
    )


def _honest_inventory(raw: dict[str, Any], *, kind: str) -> dict[str, Any]:
    out = dict(raw)
    backend = str(out.get("backend") or getattr(get_backend(), "name", ""))
    if backend == "fake" or out.get("fixture") is True:
        out.setdefault("source", "fixture")
        out["fixture"] = True
        out["ok"] = True
        return out
    # Real backends: require a hostname/os from the platform, else unavailable.
    if not out.get("hostname") and not out.get("os"):
        return {
            "ok": False,
            "code": "SYSTEM_INFO_UNAVAILABLE",
            "error": f"{kind}: backend returned empty inventory",
            "source": backend or "unknown",
        }
    out.setdefault("source", backend or "backend")
    out["ok"] = True
    return out


def _honest_resources(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    backend = getattr(get_backend(), "name", "")
    note = str(out.get("note") or out.get("error") or "")
    mem = out.get("memory") if isinstance(out.get("memory"), dict) else {}
    # psutil-missing shape: zeros + empty memory + note — anche se il nome
    # backend e' fake (test/monkeypatch): unavailable vince sul fixture.
    unavailable = (
        "unavailable" in note.lower()
        or out.get("ok") is False
        or out.get("code") == "RESOURCES_UNAVAILABLE"
        or (not mem and out.get("cpu_percent") in (0, 0.0) and "note" in out)
    )
    if unavailable:
        return {
            "ok": False,
            "code": "RESOURCES_UNAVAILABLE",
            "error": note or "resource counters unavailable (no invented zeros)",
            "source": backend or "unknown",
            "detail": {k: out[k] for k in ("note", "error") if k in out},
        }
    if backend == "fake" or out.get("fixture") is True:
        out.setdefault("source", "fixture")
        out["fixture"] = True
        out["ok"] = True
        return out
    if not mem.get("total_mb") and not mem.get("total"):
        return {
            "ok": False,
            "code": "RESOURCES_UNAVAILABLE",
            "error": "memory totals missing — refusing invented idle zeros",
            "source": backend or "unknown",
        }
    out.setdefault("source", "psutil" if backend in {"linux", "windows"} else backend)
    out["ok"] = True
    return out


def _honest_uptime(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    backend = getattr(get_backend(), "name", "")
    if backend == "fake" or out.get("fixture") is True:
        out.setdefault("source", "fixture")
        out["fixture"] = True
        out["ok"] = True
        return out
    secs = out.get("uptime_seconds")
    if secs is None and out.get("uptime_sec") is None:
        return {
            "ok": False,
            "code": "UPTIME_UNAVAILABLE",
            "error": "uptime unavailable",
            "source": backend or "unknown",
        }
    out.setdefault("source", "psutil" if backend in {"linux", "windows"} else backend)
    out["ok"] = True
    return out


def capabilities() -> dict[str, Any]:
    b = get_backend()
    flags: dict[str, bool]
    if hasattr(b, "capability_flags"):
        flags = dict(b.capability_flags())
    elif getattr(b, "name", "") == "fake":
        # Il backend fake non ha `capability_flags`: la sua tabella e' qui, ed e'
        # legittimamente una fixture — supporta tutto perche' tutto e' simulato.
        flags = {
            "processes": True,
            "filesystem": True,
            "network": True,
            "system": True,
            "windows_ui": True,
            "atspi": False,
            "clipboard": True,
            "screenshot": True,
            "audio": True,
            "services": True,
            "service_control": True,
            "sessions": True,
            "devices": True,
            "printers": True,
            "users": True,
            "displays": True,
            "drives": True,
            "registry_compat": True,
            "windows_uia": False,
            "fake_crm": True,
            "ocr": True,
            "ocr_tesseract": False,
            "wayland": False,
            "x11": False,
            "privileged": False,
            "vision": True,
        }
    else:
        flags = {"system": True}

    # NOTA — qui c'era una terza tabella, `elif backend == "windows"`, hardcoded.
    # Era IRRAGGIUNGIBILE: `WindowsBackend` espone `capability_flags`, quindi il
    # primo ramo la intercetta sempre. E dichiarava `"services": False` — falso
    # da quando il backend enumera davvero i servizi via il Service Control
    # Manager. Una tabella morta che dice il falso e' peggio di nessuna tabella:
    # nessuno la corregge, perche' nessuno la vede sbagliare. Un test verifica
    # che non torni (`test_capability_flags_contract.py`).

    feature_list = [
        "system",
        "processes",
        "apps",
        "windows",
        "ui",
        "input",
        "clipboard",
        "display",
        "filesystem",
        "storage",
        "network",
        "services",
        "audio",
        "devices",
        "printers",
        "users",
        "registry",
        "terminal",
        "adapters",
        "workflows",
        "mcp",
        "websocket",
        "metrics",
    ]
    # Honest: drop UI/window features when flags say unavailable
    if flags.get("windows_ui") is False:
        # still expose the API surface, but flag honesty in feature_flags
        pass

    return {
        "backend": b.name,
        "features": feature_list,
        "feature_flags": flags,
        "permissions": [
            "system.read",
            "filesystem.read",
            "filesystem.write",
            "process.read",
            "process.execute",
            "ui.read",
            "ui.control",
            "network.read",
            "service.control",
            "admin",
        ],
        "notes": {
            "windows_uia_on_linux": False,
            "adapter_ui": (
                "Use WINOS_BACKEND=fake for Contoso CRM UI-tree demos; "
                "LinuxBackend adapters fall back to vision/OCR when AT-SPI tree is empty."
            ),
            "wayland": "Compositor-specific tools (ydotool/wtype, wlrctl/swaymsg/hyprctl); portals limited",
            "ocr": "tesseract preferred; Pillow template fallback always available",
            "privileged": "Requires ADMIN + WINOS_ALLOW_PRIVILEGED=true; never silent root",
        },
    }
