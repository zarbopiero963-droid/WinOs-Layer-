"""N004 — runtime health / readiness envelope (H63-N004 / #67).

Distinguishes process liveness from backend readiness. `status: ok` is only
returned when a real backend can be resolved — never when the backend is
unavailable, misconfigured, or recovery has not completed.

Does NOT replace adapter/API states. Does NOT claim installed W/L or
MANUAL_ONLY PASS.
"""
from __future__ import annotations

from typing import Any

from windows_os_api import __version__

BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
CONFIG_INVALID = "CONFIG_INVALID"


def probe_runtime_health(*, get_backend_fn=None, get_settings_fn=None) -> dict[str, Any]:
    """Return the health envelope for GET /v1/health.

    Additive keys: `version` stays; `ready`, `backend`, `error_code`, `reason`
    appear when useful. Callers must not treat `status != "ok"` as success.
    """
    get_backend_fn = get_backend_fn or _default_get_backend
    get_settings_fn = get_settings_fn or _default_get_settings

    try:
        settings = get_settings_fn()
    except Exception as exc:  # noqa: BLE001 — corrupt/invalid config
        return {
            "status": "unavailable",
            "version": __version__,
            "ready": False,
            "error_code": CONFIG_INVALID,
            "reason": str(exc),
        }

    try:
        backend = get_backend_fn()
    except Exception as exc:  # noqa: BLE001 — BackendUnavailable and peers
        return {
            "status": "unavailable",
            "version": __version__,
            "ready": False,
            "backend_requested": getattr(settings, "backend", None),
            "error_code": BACKEND_UNAVAILABLE,
            "reason": str(exc),
        }

    name = getattr(backend, "name", type(backend).__name__)
    return {
        "status": "ok",
        "version": __version__,
        "ready": True,
        "backend": name,
    }


def _default_get_settings():
    from windows_os_api.core.runtime.config import get_settings

    return get_settings()


def _default_get_backend():
    from windows_os_api.backends.factory import get_backend

    return get_backend()
