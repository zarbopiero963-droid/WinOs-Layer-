"""Security, remote policy, trust, metrics, audit, auth revoke/rotate (N012)."""
from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException

from windows_os_api.core.permissions.model import Permission, Role
from windows_os_api.core.security.auth import (
    AuthContext,
    require_permission,
    authenticate,
    get_auth_registry,
    resolve_role,
    PLACEHOLDER_API_KEYS,
)
from windows_os_api.core.security.audit import get_audit_logger
from windows_os_api.core.runtime.config import get_settings
from windows_os_api.apps.trust.signing import sign_adapter_manifest, verify_adapter_signature, resolve_trust_level
from windows_os_api.apps.sandbox.permissions import SandboxPolicy, set_policy, get_policy
from windows_os_api.observability.metrics import get_metrics
from windows_os_api.api.rest.deps import audit as audit_event

router = APIRouter(tags=["security"])

class TrustBody(BaseModel):
    manifest: dict
    signature: str | None = None

class SignBody(BaseModel):
    manifest: dict

class PolicyBody(BaseModel):
    app_id: str
    allowed_actions: list[str] = []
    denied_actions: list[str] = []
    max_risk: str = "medium"

class RevokeKeyBody(BaseModel):
    api_key: str

class RevokeSessionBody(BaseModel):
    session_id: str

class RotateBody(BaseModel):
    old_api_key: str
    new_api_key: str

class ScopeBody(BaseModel):
    api_key: str
    app_ids: list[str] = Field(default_factory=list)
    user_id: str | None = None

@router.get("/auth/me")
def me(auth: AuthContext = Depends(authenticate)):
    return {
        "subject": auth.subject,
        "role": auth.role.value,
        "permissions": sorted(p.value for p in auth.permissions),
        "session_id": auth.session_id,
        "user_id": auth.user_id,
        "app_scopes": sorted(auth.app_scopes),
        "key_fingerprint": auth.key_fingerprint[:16] if auth.key_fingerprint else "",
    }

@router.post("/auth/revoke")
def revoke_key(
    body: RevokeKeyBody,
    auth: AuthContext = Depends(require_permission(Permission.ADMIN)),
):
    """Revoke a configured (or overlay) API key. ADMIN only — separation of duties."""
    if not body.api_key:
        raise HTTPException(400, "api_key required")
    # SoD: ADMIN may not use this path to launder placeholder confusion silently.
    result = get_auth_registry().revoke_key(body.api_key)
    audit_event("auth.revoke", auth, resource=result.get("key_fingerprint", ""), detail=result)
    return result

@router.post("/auth/revoke-session")
def revoke_session(
    body: RevokeSessionBody,
    auth: AuthContext = Depends(authenticate),
):
    """Revoke a session. Own session allowed; others require ADMIN."""
    registry = get_auth_registry()
    sess = registry.get_session(body.session_id)
    if sess is None:
        raise HTTPException(404, "Unknown session")
    if sess.key_fingerprint != auth.key_fingerprint and auth.role != Role.ADMIN:
        raise HTTPException(403, "Cannot revoke another principal's session")
    result = registry.revoke_session(body.session_id)
    audit_event("auth.revoke_session", auth, resource=body.session_id, detail=result)
    return result

@router.post("/auth/rotate")
def rotate_key(
    body: RotateBody,
    auth: AuthContext = Depends(require_permission(Permission.ADMIN)),
):
    """Rotate: revoke old key, register new overlay with the *same* role as old.

    ADMIN only (SoD). Role cannot be escalated via rotate — derived from old key.
    """
    settings = get_settings()
    try:
        old_role = resolve_role(body.old_api_key, settings)
    except HTTPException:
        # Old may already be revoked but we still need its prior role from overlay/settings.
        # If fully unknown, refuse.
        raise HTTPException(400, "old_api_key is not a known active credential") from None
    if body.new_api_key in PLACEHOLDER_API_KEYS:
        raise HTTPException(400, "Placeholder keys cannot be rotation targets")
    # SoD: new material must not already map to a (possibly different) role via Settings.
    from windows_os_api.core.security.auth import key_in_settings
    if key_in_settings(body.new_api_key, settings):
        raise HTTPException(400, "new_api_key already configured in Settings")
    result = get_auth_registry().rotate_key(body.old_api_key, body.new_api_key, old_role)
    audit_event("auth.rotate", auth, resource=result.get("new_key_fingerprint", ""), detail={
        "old": result.get("old_key_fingerprint"),
        "role": old_role.value,
    })
    return result

@router.get("/auth/sessions")
def list_own_sessions(auth: AuthContext = Depends(authenticate)):
    """List sessions for the caller's key fingerprint (own isolation surface)."""
    if not auth.key_fingerprint:
        return {"sessions": []}
    return {"sessions": get_auth_registry().list_sessions_for_fingerprint(auth.key_fingerprint)}

@router.put("/auth/scopes")
def set_scopes(
    body: ScopeBody,
    auth: AuthContext = Depends(require_permission(Permission.ADMIN)),
):
    """Bind app scopes / user_id to a key (ADMIN). Empty app_ids = unrestricted."""
    registry = get_auth_registry()
    scopes = registry.set_app_scopes(body.api_key, body.app_ids)
    user = None
    if body.user_id is not None:
        user = registry.set_user_id(body.api_key, body.user_id)
    out = {"app_scopes": sorted(scopes), "user_id": user}
    audit_event("auth.scopes", auth, resource=body.api_key[:8] + "...", detail=out)
    return out

@router.get("/audit")
def audit_log(auth: AuthContext = Depends(require_permission(Permission.ADMIN)), limit: int = 100):
    entries = get_audit_logger().read_all()
    return {"entries": entries[-limit:]}

@router.get("/remote/policy")
def remote_policy(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    s = get_settings()
    return {
        "remote_access_enabled": s.remote_access_enabled,
        "host": s.effective_host(),
        "allowed_hosts": s.allowed_hosts,
        "default": "localhost-only",
    }

@router.post("/trust/sign")
def trust_sign(body: SignBody, auth: AuthContext = Depends(require_permission(Permission.ADMIN))):
    sig = sign_adapter_manifest(body.manifest)
    return {"signature": sig, "trust_level": "verified"}

@router.post("/trust/verify")
def trust_verify(body: TrustBody, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_USE))):
    ok = bool(body.signature) and verify_adapter_signature(body.manifest, body.signature or "")
    level = resolve_trust_level(body.manifest, body.signature)
    return {"valid": ok, "trust_level": level}

@router.put("/sandbox/policy")
def sandbox_policy(body: PolicyBody, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_MANAGE))):
    pol = set_policy(SandboxPolicy(
        app_id=body.app_id,
        allowed_actions=set(body.allowed_actions),
        denied_actions=set(body.denied_actions),
        max_risk=body.max_risk,
    ))
    return {"app_id": pol.app_id, "max_risk": pol.max_risk, "allowed": list(pol.allowed_actions), "denied": list(pol.denied_actions)}

@router.get("/sandbox/policy/{app_id}")
def get_sandbox(app_id: str, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_USE))):
    pol = get_policy(app_id)
    return {"app_id": pol.app_id, "max_risk": pol.max_risk, "allowed": list(pol.allowed_actions), "denied": list(pol.denied_actions)}

@router.get("/metrics")
def metrics(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return get_metrics().snapshot()
