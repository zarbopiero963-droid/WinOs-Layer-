"""N004/N043 — runtime health: liveness vs readiness (backend/UI).

Liveness = process is up (always true if this code runs).
Readiness = backend constructible; UI = control_center index present.
`status: ok` only when backend is ready — never when backend is unavailable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from windows_os_api import __version__

BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
CONFIG_INVALID = "CONFIG_INVALID"
UI_UNAVAILABLE = "UI_UNAVAILABLE"


def probe_liveness() -> dict[str, Any]:
    """Process liveness only — never claims backend/UI readiness."""
    return {
        "status": "alive",
        "live": True,
        "version": __version__,
    }


def probe_ui_ready(*, control_center_index: Path | None = None) -> dict[str, Any]:
    if control_center_index is not None:
        idx = control_center_index
    else:
        # windows_os_api/control_center/index.html (parents: os -> windows_os_api)
        # Audit H63-N043: parents[2]/repo_root/control_center was wrong → false UI_UNAVAILABLE.
        idx = Path(__file__).resolve().parents[1] / "control_center" / "index.html"
    ready = idx.is_file()
    out: dict[str, Any] = {"ready": ready, "path": str(idx.name)}
    if not ready:
        out["error_code"] = UI_UNAVAILABLE
        out["reason"] = "control_center_index_missing"
    return out


def probe_runtime_health(*, get_backend_fn=None, get_settings_fn=None) -> dict[str, Any]:
    """Backend readiness envelope (N004). Additive component fields for N043."""
    get_backend_fn = get_backend_fn or _default_get_backend
    get_settings_fn = get_settings_fn or _default_get_settings

    live = True
    ui = probe_ui_ready()

    try:
        settings = get_settings_fn()
    except Exception as exc:  # noqa: BLE001 — corrupt/invalid config
        return {
            "status": "unavailable",
            "version": __version__,
            "live": live,
            "ready": False,
            "error_code": CONFIG_INVALID,
            "reason": str(exc),
            "components": {
                "backend": {"ready": False, "error_code": CONFIG_INVALID, "reason": str(exc)},
                "ui": ui,
            },
        }

    try:
        backend = get_backend_fn()
    except Exception as exc:  # noqa: BLE001 — BackendUnavailable and peers
        return {
            "status": "unavailable",
            "version": __version__,
            "live": live,
            "ready": False,
            "backend_requested": getattr(settings, "backend", None),
            "error_code": BACKEND_UNAVAILABLE,
            "reason": str(exc),
            "components": {
                "backend": {
                    "ready": False,
                    "error_code": BACKEND_UNAVAILABLE,
                    "reason": str(exc),
                },
                "ui": ui,
            },
        }

    name = getattr(backend, "name", type(backend).__name__)
    backend_comp = {"ready": True, "name": name}
    # Overall ready = backend only (N004). UI is reported separately (N043).
    return {
        "status": "ok",
        "version": __version__,
        "live": live,
        "ready": True,
        "backend": name,
        "components": {"backend": backend_comp, "ui": ui},
    }


def probe_readiness(*, get_backend_fn=None, get_settings_fn=None) -> dict[str, Any]:
    """Explicit readiness: same backend contract as health, components split."""
    return probe_runtime_health(
        get_backend_fn=get_backend_fn, get_settings_fn=get_settings_fn
    )


def _default_get_settings():
    from windows_os_api.core.runtime.config import get_settings

    return get_settings()


def _default_get_backend():
    from windows_os_api.backends.factory import get_backend

    return get_backend()
