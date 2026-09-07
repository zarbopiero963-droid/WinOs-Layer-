"""Window manager APIs."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission
from windows_os_api.os.windows import service as wins

router = APIRouter(prefix="/windows", tags=["windows"])


class MoveBody(BaseModel):
    x: int
    y: int


class ResizeBody(BaseModel):
    width: int
    height: int

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


# Geometry / state. All five return the geometry (and, where it applies, the
# state) the OS reports AFTER the operation — `requested` and `geometry` are
# separate fields on purpose, because a window manager is free to clamp,
# quantise or offset what it was asked for.
@router.post("/{hwnd}/move")
def move(hwnd: int, body: MoveBody,
         auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return wins.move_window(hwnd, body.x, body.y)


@router.post("/{hwnd}/resize")
def resize(hwnd: int, body: ResizeBody,
           auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return wins.resize_window(hwnd, body.width, body.height)


@router.post("/{hwnd}/minimize")
def minimize(hwnd: int,
             auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return wins.minimize_window(hwnd)


@router.post("/{hwnd}/maximize")
def maximize(hwnd: int,
             auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return wins.maximize_window(hwnd)


@router.post("/{hwnd}/restore")
def restore(hwnd: int,
            auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return wins.restore_window(hwnd)
