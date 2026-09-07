"""UI tree / accessible control / input / clipboard / display."""
from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends
from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission
from windows_os_api.apps.ui_inspector import service as ui
from windows_os_api.os.input import service as inp
from windows_os_api.os.display import service as disp

router = APIRouter(tags=["ui"])


class MouseMove(BaseModel):
    x: int
    y: int


class MouseClick(BaseModel):
    x: int
    y: int
    button: str = "left"


class KeyPress(BaseModel):
    key: str
    modifiers: list[str] = []


class TypeText(BaseModel):
    text: str


class ClipboardSet(BaseModel):
    text: str


class AccessibleFind(BaseModel):
    name: str | None = None
    role: str | None = None
    exact: bool = False


class AccessibleClick(BaseModel):
    name: str
    role: str | None = None


class AccessibleSetText(BaseModel):
    name: str
    text: str
    role: str | None = None


@router.get("/ui/tree")
def ui_tree(hwnd: int | None = None, auth: AuthContext = Depends(require_permission(Permission.UI_READ))):
    return ui.get_tree(hwnd)


@router.get("/ui/find")
def ui_find(
    name: str | None = None,
    role: str | None = None,
    exact: bool = False,
    auth: AuthContext = Depends(require_permission(Permission.UI_READ)),
):
    node = ui.find_accessible(name=name, role=role, exact=exact)
    return {"ok": node is not None, "node": node}


@router.post("/ui/find")
def ui_find_post(
    body: AccessibleFind,
    auth: AuthContext = Depends(require_permission(Permission.UI_READ)),
):
    node = ui.find_accessible(name=body.name, role=body.role, exact=body.exact)
    return {"ok": node is not None, "node": node}


@router.post("/ui/click")
def ui_accessible_click(
    body: AccessibleClick,
    auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL)),
):
    return ui.accessible_click(body.name, role=body.role)


@router.post("/ui/set-text")
def ui_accessible_set_text(
    body: AccessibleSetText,
    auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL)),
):
    return ui.accessible_set_text(body.name, body.text, role=body.role)


@router.post("/input/mouse/move")
def mouse_move(body: MouseMove, auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return inp.mouse_move(body.x, body.y)


@router.post("/input/mouse/click")
def mouse_click(body: MouseClick, auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return inp.mouse_click(body.x, body.y, body.button)


@router.post("/input/keyboard/key")
def key_press(body: KeyPress, auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return inp.key_press(body.key, body.modifiers)


@router.post("/input/keyboard/type")
def type_text(body: TypeText, auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return inp.type_text(body.text)


@router.get("/clipboard")
def clipboard_get(auth: AuthContext = Depends(require_permission(Permission.UI_READ))):
    return inp.clipboard_get()


@router.put("/clipboard")
def clipboard_set(body: ClipboardSet, auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return inp.clipboard_set(body.text)


@router.get("/displays")
def displays(auth: AuthContext = Depends(require_permission(Permission.UI_READ))):
    return {"displays": disp.list_displays()}


@router.get("/displays/screenshot")
def screenshot(display_id: int | None = None, auth: AuthContext = Depends(require_permission(Permission.UI_READ))):
    return disp.screenshot(display_id)
