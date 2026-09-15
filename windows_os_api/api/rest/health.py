"""Health / live / ready / system / capabilities (N004 + N043)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission
from windows_os_api.os.runtime_health import probe_liveness, probe_runtime_health
from windows_os_api.os.system import service as system

router = APIRouter(tags=["health"])


def _health_payload() -> dict:
    """Probe backend readiness. Never claim ok when the backend cannot run."""
    return probe_runtime_health()


@router.get("/live")
def live():
    """N043 — process liveness only. Always 200 if the process answers.

    Never encodes backend/UI readiness; use /ready or /health for that.
    """
    return probe_liveness()


@router.get("/health")
def health():
    """Readiness-aware health: status is not ok when the OS backend is unavailable.

    Additive fields: live, ready, components.backend/ui, backend, error_code/reason.
    Version is always present. HTTP 503 when backend cannot be constructed so
    probes that only check status codes also see the failure.
    """
    body = _health_payload()
    if body.get("ready") is False:
        return JSONResponse(status_code=503, content=body)
    return body


@router.get("/ready")
def ready():
    """N043 — explicit readiness (backend + UI components; overall = backend)."""
    body = _health_payload()
    if body.get("ready") is False:
        return JSONResponse(status_code=503, content=body)
    return body


@router.get("/system")
def get_system(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return system.system_info()


@router.get("/capabilities")
def get_capabilities(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return system.capabilities()


@router.get("/system/resources")
def get_resources(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return system.resources()


@router.get("/system/uptime")
def get_uptime(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return system.uptime()


@router.post("/system/power/{action}")
def power(action: str, auth: AuthContext = Depends(require_permission(Permission.ADMIN))):
    return system.power(action)
