"""Services / audio / devices / printers / users / registry / terminal."""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel
from fastapi import APIRouter, Depends
from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission
from windows_os_api.api.rest.deps import audit
from windows_os_api.os.services import service as svcs
from windows_os_api.os.audio import service as audio
from windows_os_api.os.devices import service as devices
from windows_os_api.os.printers import service as printers
from windows_os_api.os.users import service as users
from windows_os_api.os.registry import service as registry
from windows_os_api.os.terminal import service as terminal

router = APIRouter(tags=["os-extended"])

class ServiceAction(BaseModel):
    action: str
    scope: str = "user"

class VolumeBody(BaseModel):
    volume: int

class MuteBody(BaseModel):
    muted: bool

class ElevateBody(BaseModel):
    argv: list[str]

class RegistryWrite(BaseModel):
    path: str
    name: str
    value: Any

class TerminalBody(BaseModel):
    command: str
    policy: str = "ALLOW"

@router.get("/services")
def list_services(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return {"services": svcs.list_services()}

@router.post("/services/{name}")
def control_service(name: str, body: ServiceAction, auth: AuthContext = Depends(require_permission(Permission.SERVICE_CONTROL))):
    result = svcs.control(name, body.action, scope=body.scope)
    audit("service.control", auth, resource=name, detail=result)
    return result

@router.get("/audio/devices")
def audio_devices(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return {"devices": audio.devices()}

@router.get("/audio/volume")
def audio_volume(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return audio.volume()

@router.put("/audio/volume")
def audio_set_volume(body: VolumeBody, auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    result = audio.set_volume(body.volume)
    audit("audio.volume", auth, resource="default", detail=result)
    return result

@router.put("/audio/mute")
def audio_set_mute(body: MuteBody, auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    result = audio.set_mute(body.muted)
    audit("audio.mute", auth, resource="default", detail=result)
    return result

@router.get("/devices")
def list_devices(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return {"devices": devices.list_devices()}

@router.get("/printers")
def list_printers(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return {"printers": printers.list_printers()}

@router.get("/users")
def list_users(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return {"users": users.list_users()}

@router.get("/sessions")
def list_sessions(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return {"sessions": users.list_sessions()}

@router.get("/session")
def get_session(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return users.session_info()

@router.post("/privilege/elevate")
def privilege_elevate(body: ElevateBody, auth: AuthContext = Depends(require_permission(Permission.ADMIN))):
    """Gated elevation: requires ADMIN role + WINOS_ALLOW_PRIVILEGED=true."""
    from windows_os_api.core.security.privilege import attempt_elevation
    from windows_os_api.core.permissions.model import Permission as P

    result = attempt_elevation(
        list(body.argv),
        auth_has_admin=auth.check(P.ADMIN),
        subject=auth.subject,
        action="api.elevate",
    )
    audit(
        "privilege.elevate",
        auth,
        resource=(body.argv[0] if body.argv else ""),
        detail={"ok": result.get("ok"), "code": result.get("code")},
        outcome="success" if result.get("ok") else "denied",
    )
    return result

@router.get("/registry")
def registry_read(path: str, name: str | None = None, auth: AuthContext = Depends(require_permission(Permission.REGISTRY_READ))):
    return registry.read(path, name)

@router.put("/registry")
def registry_write(body: RegistryWrite, auth: AuthContext = Depends(require_permission(Permission.REGISTRY_WRITE))):
    result = registry.write(body.path, body.name, body.value)
    audit("registry.write", auth, resource=body.path, detail={"name": body.name})
    return result

@router.post("/terminal/execute")
def terminal_execute(body: TerminalBody, auth: AuthContext = Depends(require_permission(Permission.TERMINAL_EXECUTE))):
    if body.policy.upper() == "ADMIN" and not auth.check(Permission.ADMIN):
        from fastapi import HTTPException
        raise HTTPException(403, "ADMIN policy requires admin role")
    result = terminal.execute(body.command, body.policy)
    audit("terminal.execute", auth, resource=body.command[:80], detail={"policy": body.policy, "ok": result.get("ok")})
    return result
