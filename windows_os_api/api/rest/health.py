"""Health / system / capabilities."""
from __future__ import annotations
from fastapi import APIRouter, Depends
from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission
from windows_os_api.os.system import service as system
from windows_os_api import __version__

router = APIRouter(tags=["health"])

@router.get("/health")
def health():
    return {"status": "ok", "version": __version__}

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
