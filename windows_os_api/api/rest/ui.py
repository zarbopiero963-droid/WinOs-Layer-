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


class Scroll(BaseModel):
    direction: str = "down"
    amount: int = 3
    x: int | None = None
    y: int | None = None


class MouseDrag(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int
    button: str = "left"
    steps: int = 10


class KeyName(BaseModel):
    key: str


class Hotkey(BaseModel):
    keys: list[str]


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


# The rest of the input primitives. `ok` means the OS accepted the event, not
# that the focused window did anything with it — there is no readback for a
# keystroke. The pointer is the exception: move and drag report `position`.
@router.post("/input/mouse/double-click")
def double_click(body: MouseClick,
                 auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return inp.double_click(body.x, body.y, body.button)


@router.post("/input/mouse/scroll")
def scroll(body: Scroll,
           auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return inp.scroll(body.direction, body.amount, body.x, body.y)


@router.post("/input/mouse/drag")
def mouse_drag(body: MouseDrag,
               auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return inp.mouse_drag(body.x1, body.y1, body.x2, body.y2, body.button, body.steps)


@router.get("/input/mouse/position")
def pointer_position(auth: AuthContext = Depends(require_permission(Permission.UI_READ))):
    position = inp.pointer_position()
    return {"ok": position is not None, "position": position}


@router.post("/input/keyboard/down")
def key_down(body: KeyName,
             auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return inp.key_down(body.key)


@router.post("/input/keyboard/up")
def key_up(body: KeyName,
           auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return inp.key_up(body.key)


@router.post("/input/keyboard/hotkey")
def hotkey(body: Hotkey,
           auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return inp.hotkey(body.keys)


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


class VisionFind(BaseModel):
    text: str


class VisionClick(BaseModel):
    text: str
    dry_run: bool = True


@router.post("/ui/vision/find")
def vision_find(body: VisionFind, auth: AuthContext = Depends(require_permission(Permission.UI_READ))):
    return ui.find_text_vision(body.text)


@router.post("/ui/vision/click")
def vision_click(body: VisionClick, auth: AuthContext = Depends(require_permission(Permission.UI_CONTROL))):
    return ui.click_text_vision(body.text, dry_run=body.dry_run)
