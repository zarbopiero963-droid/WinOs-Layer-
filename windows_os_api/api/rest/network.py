"""Network APIs."""
from __future__ import annotations
from fastapi import APIRouter, Depends
from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission
from windows_os_api.os.network import service as net

router = APIRouter(prefix="/network", tags=["network"])

@router.get("/interfaces")
def interfaces(auth: AuthContext = Depends(require_permission(Permission.NETWORK_READ))):
    return {"interfaces": net.interfaces()}

@router.get("/connections")
def connections(auth: AuthContext = Depends(require_permission(Permission.NETWORK_READ))):
    return {"connections": net.connections()}
