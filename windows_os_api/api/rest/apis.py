"""N016/N018 — REST API catalog + API Test.

Catalog: GET /v1/apis list + detail over PersistentApiRegistry / ApiRegistry.
Pagination uses ``limit``/``offset``. Missing record → 404; registry
unavailable → 503. Cross-app filter outside caller scopes → 403.

N018: ``POST /v1/apis/{api_id}/test`` runs gateway execute + independent
postcondition; HTTP 200 alone is never verified success.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from windows_os_api.api.rest.deps import audit
from windows_os_api.apps.api_registry.api_test import run_api_test
from windows_os_api.apps.api_registry.catalog import (
    CatalogScopeDenied,
    RegistryUnavailable,
    get_catalog_record,
    list_catalog,
    resolve_registry,
)
from windows_os_api.core.permissions.model import Permission, Role
from windows_os_api.core.security.auth import (
    AuthContext,
    ensure_app_access,
    get_auth_registry,
    require_permission,
)

router = APIRouter(prefix="/apis", tags=["apis"])


def _visible_app_ids(auth: AuthContext) -> frozenset[str] | None:
    """None = unrestricted (ADMIN or no scopes bound)."""
    if auth.role == Role.ADMIN or Permission.ADMIN in auth.permissions:
        return None
    registry = get_auth_registry()
    scopes = (
        registry.get_app_scopes_fp(auth.key_fingerprint)
        if auth.key_fingerprint
        else auth.app_scopes
    )
    if not scopes:
        return None
    return frozenset(scopes)


def _parse_permissions_csv(raw: str | None) -> list[str] | None:
    if raw is None or not str(raw).strip():
        return None
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    return parts or None


@router.get("")
def list_apis(
    auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ)),
    q: str | None = Query(default=None, description="Case-insensitive search over id/name/path/capability"),
    source: str | None = Query(default=None),
    application_id: str | None = Query(default=None),
    app: str | None = Query(default=None, description="Alias for application_id"),
    status_filter: str | None = Query(default=None, alias="status"),
    permission: str | None = Query(default=None),
    permissions: str | None = Query(default=None, description="Comma-separated permission names (OR)"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List catalog entries with search, filters, and limit/offset pagination."""
    visible = _visible_app_ids(auth)
    app_id = (application_id or app or "").strip() or None
    try:
        page = list_catalog(
            q=q,
            source=source,
            application_id=app_id,
            status=status_filter,
            permission=permission,
            permissions=_parse_permissions_csv(permissions),
            visible_app_ids=visible,
            limit=limit,
            offset=offset,
        )
    except CatalogScopeDenied as exc:
        audit("apis.list", auth, outcome="denied", detail={"reason": str(exc)})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except RegistryUnavailable as exc:
        audit("apis.list", auth, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API registry unavailable",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    audit("apis.list", auth, detail={"total": page.total, "limit": page.limit, "offset": page.offset})
    return page.to_dict()


@router.get("/{api_id}")
def get_api(
    api_id: str,
    auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ)),
):
    """Return one catalog record. 404 if missing/hidden; 503 if registry down."""
    visible = _visible_app_ids(auth)
    try:
        rec = get_catalog_record(api_id, visible_app_ids=visible)
    except RegistryUnavailable as exc:
        audit("apis.get", auth, resource=api_id, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API registry unavailable",
        ) from exc

    if rec is None:
        audit("apis.get", auth, resource=api_id, outcome="failure", detail={"reason": "not_found"})
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API not found")

    audit("apis.get", auth, resource=api_id)
    return rec.to_dict()


class ApiTestBody(BaseModel):
    """Optional params for API Test execution + postcondition."""

    params: dict[str, Any] = Field(default_factory=dict)
    update_registry_on_pass: bool = True


@router.post("/{api_id}/test")
def test_api_route(
    api_id: str,
    body: ApiTestBody | None = None,
    auth: AuthContext = Depends(require_permission(Permission.ADAPTER_USE)),
):
    """N018 API Test: execute via gateway + independent postcondition.

    Returns HTTP 200 with a body where ``success`` / ``verification.verified``
    reflect observed effect — never treat transport 200 as operational success
    (H63-N018 / #61 §20). Missing API → 404. Registry down → 503.
    """
    body = body or ApiTestBody()
    try:
        resolve_registry()
    except RegistryUnavailable as exc:
        audit("apis.test", auth, resource=api_id, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API registry unavailable",
        ) from exc

    visible = _visible_app_ids(auth)
    try:
        rec = get_catalog_record(api_id, visible_app_ids=visible)
    except RegistryUnavailable as exc:
        audit("apis.test", auth, resource=api_id, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API registry unavailable",
        ) from exc
    if rec is None:
        audit("apis.test", auth, resource=api_id, outcome="failure", detail={"reason": "not_found"})
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API not found")

    if rec.application_id:
        ensure_app_access(auth, rec.application_id)

    result = run_api_test(
        rec.id,
        params=body.params,
        update_registry_on_pass=body.update_registry_on_pass,
    )
    outcome = "success" if result.get("success") else "failure"
    if (result.get("verification") or {}).get("status") == "BLOCKED":
        outcome = "denied"
    audit("apis.test", auth, resource=api_id, outcome=outcome, detail=result)
    return result
