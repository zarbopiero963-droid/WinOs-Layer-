"""N016/N018/N019/N023/N024/N025 — REST API catalog + test + disable + create/publish + SDK.

Catalog: GET /v1/apis list + detail over PersistentApiRegistry / ApiRegistry.
Pagination uses ``limit``/``offset``. Missing record → 404; registry
unavailable → 503. Cross-app filter outside caller scopes → 403.

N018: ``POST /v1/apis/{api_id}/test`` runs gateway execute + independent
postcondition; HTTP 200 alone is never verified success.

N023: ``POST /v1/apis/{api_id}/disable`` sets registry status DISABLED
(ADAPTER_MANAGE); gateway then fail-closes Try it / execute.

N024: ``POST /v1/apis`` creates a PARTIAL candidate; verify attaches evidence
without publishing; ``POST /v1/apis/{api_id}/publish`` promotes to VERIFIED
only with fresh verification evidence (no implicit GUI consent).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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
from windows_os_api.apps.api_registry.model import (
    ApiStatus,
    RegistrationRejected,
    authorize_verified_status,
)
from windows_os_api.apps.schema.openapi_export import (
    OpenAPISchemaRejected,
    assemble_openapi_document,
    build_registry_operation,
    finalize_openapi_export,
    http_path_is_bound,
)
from windows_os_api.apps.schema.sdk_export import (
    SdkExportRejected,
    generate_python_sdk,
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


class CreateApiBody(BaseModel):
    """N024: create registry candidate (always PARTIAL; never VERIFIED on create)."""

    name: str
    method: str
    path: str
    description: str = ""
    source: str
    application_id: str = ""
    adapter_id: str = ""
    capability: str
    permissions: list[str] = Field(default_factory=list)
    authentication_required: bool = True
    # Accepted for forward-compat / UI form; not persisted on ApiRecord yet.
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


@router.post("")
def create_api_route(
    body: CreateApiBody,
    auth: AuthContext = Depends(require_permission(Permission.ADAPTER_MANAGE)),
):
    """N024: register a PARTIAL candidate API (never VERIFIED on create).

    Requires ADAPTER_MANAGE. App scope checked when ``application_id`` is set.
    Registry unavailable → 503. RegistrationRejected → 422.
    """
    try:
        registry = resolve_registry()
    except RegistryUnavailable as exc:
        audit("apis.create", auth, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API registry unavailable",
        ) from exc

    app_id = (body.application_id or "").strip()
    if app_id:
        ensure_app_access(auth, app_id)

    payload: dict[str, Any] = {
        "name": body.name,
        "method": body.method,
        "path": body.path,
        "description": body.description,
        "source": body.source,
        "application_id": app_id,
        "adapter_id": (body.adapter_id or "").strip(),
        "capability": body.capability,
        "permissions": list(body.permissions or []),
        "authentication_required": bool(body.authentication_required),
        # Force candidate — ignore any client attempt to publish on create.
        "status": ApiStatus.PARTIAL.value,
        "verification_id": None,
        "last_verified_at": None,
        "input_schema": body.input_schema,
        "output_schema": body.output_schema,
    }

    # N024: virtual_adapter create must materialize an adapter (not record-only).
    adapter_created = False
    if (body.source or "").strip() == "virtual_adapter" and app_id:
        from windows_os_api.apps.adapters.engine import create_adapter, get_adapter

        if get_adapter(app_id) is None:
            create_adapter(app_id)
            adapter_created = True
            if not payload["adapter_id"]:
                payload["adapter_id"] = app_id

    try:
        rec = registry.register(payload)
    except RegistrationRejected as exc:
        audit("apis.create", auth, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # Defense in depth: create must never yield VERIFIED.
    if rec.status is ApiStatus.VERIFIED:
        rec = registry.set_status(rec.id, ApiStatus.PARTIAL)

    audit(
        "apis.create",
        auth,
        resource=rec.id,
        outcome="success",
        detail={
            "status": rec.status.value,
            "path": rec.path,
            "method": rec.method,
            "adapter_created": adapter_created,
            "has_input_schema": rec.input_schema is not None,
            "has_output_schema": rec.output_schema is not None,
        },
    )
    return rec.to_dict()


def _registry_openapi_document(*, visible_app_ids: frozenset[str] | None) -> dict:
    """Export OpenAPI for VERIFIED registry records only (N019 / S61-08)."""
    try:
        registry = resolve_registry()
    except RegistryUnavailable:
        raise
    paths: dict = {}
    max_age = getattr(registry, "_verification_max_age_sec", None)
    for rec in registry.list():
        if rec.status is not ApiStatus.VERIFIED:
            continue
        if not rec.verification_id:
            continue
        # N019: re-evaluate verification age at export (stale → omit).
        gate_kwargs: dict = {
            "status": ApiStatus.VERIFIED,
            "verification_id": rec.verification_id,
            "last_verified_at": rec.last_verified_at,
        }
        if max_age is not None:
            gate_kwargs["max_age_sec"] = float(max_age)
        if authorize_verified_status(**gate_kwargs) is not ApiStatus.VERIFIED:
            continue
        if visible_app_ids is not None and rec.application_id:
            if rec.application_id not in visible_app_ids:
                continue
        method = (rec.method or "POST").strip().lower()
        path = rec.path
        if not path.startswith("/"):
            continue
        # N019: omit unbound paths (no matching REST route template).
        if not http_path_is_bound(path, method):
            continue
        op = build_registry_operation(rec.to_dict())
        item = paths.setdefault(path, {})
        if method in item:
            # Collision on same path+method: reject rather than silently overwrite.
            raise OpenAPISchemaRejected(
                f"duplicate registry path operation {method.upper()} {path}"
            )
        item[method] = op
    doc = assemble_openapi_document(
        title="WinOS API Registry (VERIFIED only)",
        paths=paths,
        description=(
            "Authoritative registry export: only VERIFIED records with "
            "verification_id. Method/path reflect real CRUD mapping."
        ),
    )
    return finalize_openapi_export(doc)


@router.get("/openapi.json")
def export_registry_openapi(
    auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ)),
):
    """N019: deterministic OpenAPI export of VERIFIED registry APIs."""
    visible = _visible_app_ids(auth)
    try:
        doc = _registry_openapi_document(visible_app_ids=visible)
    except RegistryUnavailable as exc:
        audit("apis.openapi", auth, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API registry unavailable",
        ) from exc
    except OpenAPISchemaRejected as exc:
        audit("apis.openapi", auth, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid OpenAPI schema: {exc}",
        ) from exc
    audit("apis.openapi", auth, detail={"paths": len(doc.get("paths") or {})})
    return doc


@router.get("/sdk.py")
def export_registry_sdk(
    auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ)),
):
    """N025: deterministic Python SDK from VERIFIED registry OpenAPI only."""
    visible = _visible_app_ids(auth)
    try:
        doc = _registry_openapi_document(visible_app_ids=visible)
        source = generate_python_sdk(doc)
    except RegistryUnavailable as exc:
        audit("apis.sdk", auth, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API registry unavailable",
        ) from exc
    except (OpenAPISchemaRejected, SdkExportRejected) as exc:
        audit("apis.sdk", auth, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid SDK export: {exc}",
        ) from exc
    audit("apis.sdk", auth, detail={"bytes": len(source.encode("utf-8"))})
    return Response(
        content=source,
        media_type="text/x-python; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="winos_apis_sdk.py"'},
    )

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


@router.post("/{api_id}/disable")
def disable_api_route(
    api_id: str,
    auth: AuthContext = Depends(require_permission(Permission.ADAPTER_MANAGE)),
):
    """N023: set API status to DISABLED (auth + audit). Gateway fail-closes after.

    Requires ADAPTER_MANAGE (or ADMIN). Missing API → 404. Registry down → 503.
    """
    try:
        registry = resolve_registry()
    except RegistryUnavailable as exc:
        audit("apis.disable", auth, resource=api_id, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API registry unavailable",
        ) from exc

    visible = _visible_app_ids(auth)
    try:
        rec = get_catalog_record(api_id, visible_app_ids=visible)
    except RegistryUnavailable as exc:
        audit("apis.disable", auth, resource=api_id, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API registry unavailable",
        ) from exc
    if rec is None:
        audit("apis.disable", auth, resource=api_id, outcome="failure", detail={"reason": "not_found"})
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API not found")

    if rec.application_id:
        ensure_app_access(auth, rec.application_id)

    try:
        updated = registry.set_status(api_id, ApiStatus.DISABLED)
    except KeyError as exc:
        audit("apis.disable", auth, resource=api_id, outcome="failure", detail={"reason": "not_found"})
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API not found") from exc

    audit(
        "apis.disable",
        auth,
        resource=api_id,
        outcome="success",
        detail={"status": updated.status.value},
    )
    return updated.to_dict()

@router.post("/{api_id}/publish")
def publish_api_route(
    api_id: str,
    auth: AuthContext = Depends(require_permission(Permission.ADAPTER_MANAGE)),
):
    """N024: promote PARTIAL candidate to VERIFIED when fresh evidence exists.

    Requires ADAPTER_MANAGE. Missing/stale evidence or DISABLED → 409 Conflict
    (recovery = re-verify with update_registry_on_pass=false, then publish).
    """
    try:
        registry = resolve_registry()
    except RegistryUnavailable as exc:
        audit("apis.publish", auth, resource=api_id, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API registry unavailable",
        ) from exc

    visible = _visible_app_ids(auth)
    try:
        rec = get_catalog_record(api_id, visible_app_ids=visible)
    except RegistryUnavailable as exc:
        audit("apis.publish", auth, resource=api_id, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API registry unavailable",
        ) from exc
    if rec is None:
        audit("apis.publish", auth, resource=api_id, outcome="failure", detail={"reason": "not_found"})
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API not found")

    if rec.application_id:
        ensure_app_access(auth, rec.application_id)

    if rec.status is ApiStatus.DISABLED:
        audit(
            "apis.publish",
            auth,
            resource=api_id,
            outcome="failure",
            detail={"reason": "disabled_needs_recovery"},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "API is DISABLED; recovery requires re-verify "
                "(POST /test with update_registry_on_pass=false) then publish"
            ),
        )

    if not rec.verification_id or rec.last_verified_at is None:
        audit(
            "apis.publish",
            auth,
            resource=api_id,
            outcome="failure",
            detail={"reason": "missing_verification_evidence"},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Missing verification evidence; run Verify "
                "(POST /v1/apis/{id}/test with update_registry_on_pass=false) first"
            ),
        )

    gated = authorize_verified_status(
        status=ApiStatus.VERIFIED,
        verification_id=rec.verification_id,
        last_verified_at=rec.last_verified_at,
    )
    if gated is not ApiStatus.VERIFIED:
        audit(
            "apis.publish",
            auth,
            resource=api_id,
            outcome="failure",
            detail={
                "reason": "verification_evidence_not_authoritative",
                "gated_status": gated.value,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Verification evidence is missing, false, or stale; "
                "re-verify before publish"
            ),
        )

    payload = rec.to_dict()
    payload["status"] = ApiStatus.VERIFIED.value
    payload["verification_id"] = rec.verification_id
    payload["last_verified_at"] = rec.last_verified_at
    try:
        updated = registry.register(payload)
    except RegistrationRejected as exc:
        audit("apis.publish", auth, resource=api_id, outcome="failure", detail={"reason": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    if updated.status is not ApiStatus.VERIFIED:
        audit(
            "apis.publish",
            auth,
            resource=api_id,
            outcome="failure",
            detail={"reason": "register_demoted", "status": updated.status.value},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Publish refused: registry demoted status (evidence gate)",
        )

    audit(
        "apis.publish",
        auth,
        resource=api_id,
        outcome="success",
        detail={
            "status": updated.status.value,
            "verification_id": updated.verification_id,
        },
    )
    return updated.to_dict()

