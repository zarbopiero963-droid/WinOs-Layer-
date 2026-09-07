"""Security, remote policy, trust, metrics, audit."""
from __future__ import annotations
from pydantic import BaseModel
from fastapi import APIRouter, Depends
from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission, authenticate
from windows_os_api.core.security.audit import get_audit_logger
from windows_os_api.core.runtime.config import get_settings
from windows_os_api.apps.trust.signing import sign_adapter_manifest, verify_adapter_signature, resolve_trust_level
from windows_os_api.apps.sandbox.permissions import SandboxPolicy, set_policy, get_policy
from windows_os_api.observability.metrics import get_metrics

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

@router.get("/auth/me")
def me(auth: AuthContext = Depends(authenticate)):
    return {"subject": auth.subject, "role": auth.role.value, "permissions": sorted(p.value for p in auth.permissions)}

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
