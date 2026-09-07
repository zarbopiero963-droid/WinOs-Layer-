"""Window manager APIs."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission
from windows_os_api.os.windows import service as wins

router = APIRouter(prefix="/windows", tags=["windows"])

@router.get("")
def list_windows(auth: AuthContext = Depends(require_permission(Permission.UI_READ))):
    return {"windows": wins.list_windows()}

@router.get("/{hwnd}")
def get_window(hwnd: int, auth: AuthContext = Depends(require_permission(Permission.UI_READ))):
    w = wins.get_window(hwnd)
    if not w:
        raise HTTPException(404, "window not found")
    return w

@router.post("/{hwnd}/focus")
def focus(hwnd: int, auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return wins.focus_window(hwnd)

@router.delete("/{hwnd}")
def close(hwnd: int, auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return wins.close_window(hwnd)
