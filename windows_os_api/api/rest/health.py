"""Health / live / ready / diagnose / system / capabilities (N004 + N043 + N044)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
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




@router.get("/diagnose/bundle")
def diagnose_bundle(
    auth: AuthContext = Depends(require_permission(Permission.ADMIN)),
    max_bytes: int | None = None,
    include_audit: bool = True,
):
    """N044 — redacted in-memory support bundle (ADMIN only).

    Produces a limited snapshot even when the OS backend is blocked/unavailable.
    Never returns API keys or integrity secrets.
    """
    from windows_os_api.api.rest.deps import audit as audit_event
    from windows_os_api.observability.diagnose import (
        DiagnoseParamError,
        build_support_bundle,
    )
    from windows_os_api.observability.metrics import get_metrics

    try:
        bundle = build_support_bundle(
            reason="rest.diagnose_bundle",
            before_restart=False,
            max_bytes=max_bytes,
            include_audit=include_audit,
        )
    except DiagnoseParamError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_event(
        "diagnose.bundle",
        auth,
        resource=str(bundle.get("bundle_id") or ""),
        detail={
            "bundle_id": bundle.get("bundle_id"),
            "truncated": bundle.get("truncated"),
            "size_bytes": bundle.get("size_bytes"),
        },
    )
    get_metrics().incr("diagnose.bundles")
    if bundle.get("truncated"):
        get_metrics().incr("diagnose.truncated")
    return bundle


@router.post("/diagnose/collect")
def diagnose_collect(
    auth: AuthContext = Depends(require_permission(Permission.ADMIN)),
    before_restart: bool = True,
    max_bytes: int | None = None,
    output_dir: str | None = None,
):
    """N044 — write support bundle to disk before restart (ADMIN).

    ``before_restart`` defaults to True: collect first; this endpoint does **not**
    restart the service. Operators restart after a successful collect.
    """
    from windows_os_api.api.rest.deps import audit as audit_event
    from windows_os_api.observability.diagnose import (
        DiagnoseError,
        DiagnoseParamError,
        collect_before_restart,
        default_bundle_path,
        write_support_bundle,
    )

    path = default_bundle_path(output_dir)
    try:
        if before_restart:
            result = collect_before_restart(
                path,
                reason="rest.collect_before_restart",
                max_bytes=max_bytes,
                subject=auth.subject,
            )
        else:
            result = write_support_bundle(
                path,
                reason="rest.diagnose_collect",
                max_bytes=max_bytes,
                before_restart=False,
            )
            audit_event(
                "diagnose.collect",
                auth,
                resource=str(result.path),
                detail={
                    "bundle_id": result.bundle_id,
                    "bytes_written": result.bytes_written,
                    "truncated": result.truncated,
                    "before_restart": False,
                },
            )
    except DiagnoseParamError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DiagnoseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "ok": True,
        "path": str(result.path),
        "bytes_written": result.bytes_written,
        "truncated": result.truncated,
        "bundle_id": result.bundle_id,
        "before_restart": result.before_restart,
        "collected_at": result.collected_at,
    }

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
