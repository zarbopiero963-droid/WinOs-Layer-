"""Apps discovery + registry + virtual adapter APIs."""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission, ensure_app_access
from windows_os_api.api.rest.deps import audit
from windows_os_api.apps.discovery import service as discovery
from windows_os_api.apps.adapters.engine import (
    create_adapter,
    list_adapters,
    verify_and_record,
)
from windows_os_api.apps.api_registry.gateway import (
    execute_via_gateway,
    gateway_http_status,
)
from windows_os_api.apps.schema.generator import app_openapi
from windows_os_api.apps.automation.actions import discover_actions

router = APIRouter(prefix="/apps", tags=["apps"])

class RegisterApp(BaseModel):
    id: str | None = None
    name: str
    path: str = ""
    version: str = ""
    publisher: str = ""

class InvokeBody(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)

class CreateAdapterBody(BaseModel):
    hwnd: int = 1001
    trust_level: str = "unsigned"

class VerifyActionBody(BaseModel):
    times: int = Field(default=1, ge=1, le=10)

@router.get("")
def list_apps(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return {"apps": discovery.registry()}

@router.post("/discover")
def discover(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    apps = discovery.discover()
    audit("apps.discover", auth, detail={"count": len(apps)})
    return {"apps": apps}

@router.post("/register")
def register(body: RegisterApp, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_MANAGE))):
    app = discovery.register_app(body.model_dump())
    return app


@router.get("/adapters/list")
def adapters(auth: AuthContext = Depends(require_permission(Permission.ADAPTER_USE))):
    return {"adapters": list_adapters()}

@router.get("/{app_id}")
def get_app(app_id: str, auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    ensure_app_access(auth, app_id)
    app = discovery.get_app(app_id)
    if not app:
        raise HTTPException(404, "app not found")
    return app

@router.post("/{app_id}/adapter")
def make_adapter(app_id: str, body: CreateAdapterBody, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_MANAGE))):
    ensure_app_access(auth, app_id)
    adapter = create_adapter(app_id, hwnd=body.hwnd, trust_level=body.trust_level)
    audit("adapter.create", auth, resource=app_id, detail={"actions": len(adapter.actions)})
    return {"app_id": adapter.app_id, "actions": [a.name for a in adapter.actions], "trust_level": adapter.trust_level}

@router.get("/{app_id}/actions")
def actions(app_id: str, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_USE))):
    ensure_app_access(auth, app_id)
    return {"actions": discover_actions(app_id=app_id)}

@router.post("/{app_id}/actions/{action_name}")
def invoke(app_id: str, action_name: str, body: InvokeBody, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_USE))):
    """Invoke via shared N017 gateway — never creates adapters implicitly."""
    ensure_app_access(auth, app_id)
    outcome = execute_via_gateway(
        app_id=app_id,
        action_name=action_name,
        params=body.params,
    )
    http_status = gateway_http_status(outcome)
    if not outcome.get("ok"):
        audit(
            "adapter.invoke",
            auth,
            resource=f"{app_id}/{action_name}",
            outcome="denied" if outcome.get("block_kind") == "security" else "failure",
            detail=outcome,
        )
        detail = {
            "code": outcome.get("code"),
            "block_kind": outcome.get("block_kind"),
            "error": outcome.get("error") or outcome.get("message"),
            "denied": bool(outcome.get("denied")),
        }
        raise HTTPException(http_status, detail=detail)
    # Prefer nested engine result when present (gateway wraps it)
    result = outcome.get("result") if isinstance(outcome.get("result"), dict) else outcome
    audit("adapter.invoke", auth, resource=f"{app_id}/{action_name}", detail=result)
    return result

@router.post("/{app_id}/actions/{action_name}/verify")
def verify(
    app_id: str,
    action_name: str,
    body: VerifyActionBody,
    auth: AuthContext = Depends(require_permission(Permission.ADAPTER_MANAGE)),
):
    # La verifica modifica temporaneamente l'app e persiste il verdetto: per
    # questo richiede ADAPTER_MANAGE, non il solo permesso di invocazione.
    ensure_app_access(auth, app_id)
    result = verify_and_record(app_id, action_name, times=body.times)
    state = (result.get("verification") or {}).get("state")
    outcome = "success" if result.get("ok") else "failure"
    if state == "BLOCKED":
        outcome = "denied"
    audit(
        "adapter.verify",
        auth,
        resource=f"{app_id}/{action_name}",
        outcome=outcome,
        detail=result,
    )
    return result

@router.get("/{app_id}/openapi.json")
def openapi_for_app(app_id: str, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_USE))):
    ensure_app_access(auth, app_id)
    return app_openapi(app_id)
