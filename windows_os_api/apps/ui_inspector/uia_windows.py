"""Windows UI Automation helpers (uiautomation / comtypes / pywinauto).

Importable on all platforms for compile/smoke tests. Runtime UIA calls require win32.
Never returns Fake Contoso CRM data.
"""
from __future__ import annotations

import sys
from typing import Any

DEFAULT_MAX_DEPTH = 6
DEFAULT_MAX_CHILDREN = 80


def uia_available() -> bool:
    """Return True if a Windows UIA library is importable on win32."""
    if sys.platform != "win32":
        return False
    for mod in ("uiautomation", "comtypes", "pywinauto"):
        try:
            __import__(mod)
            return True
        except ImportError:
            continue
    return False


def describe_backend() -> dict[str, Any]:
    """Report which UIA backends are present (safe on Linux)."""
    found: list[str] = []
    for mod in ("uiautomation", "comtypes", "pywinauto"):
        try:
            __import__(mod)
            found.append(mod)
        except ImportError:
            pass
    preferred = None
    for cand in ("uiautomation", "comtypes", "pywinauto"):
        if cand in found:
            preferred = cand
            break
    return {
        "platform": sys.platform,
        "uia_available": bool(found) and sys.platform == "win32",
        "modules": found,
        "preferred": preferred,
    }


def _bounds_dict(left: int, top: int, right: int, bottom: int) -> dict[str, int]:
    return {
        "left": int(left),
        "top": int(top),
        "right": int(right),
        "bottom": int(bottom),
        "width": max(0, int(right) - int(left)),
        "height": max(0, int(bottom) - int(top)),
    }


def _control_type_name(raw: Any) -> str:
    if raw is None:
        return "Unknown"
    if isinstance(raw, str):
        # Strip ControlType. / UIA_ prefixes
        s = raw.replace("ControlType.", "").replace("UIA_", "").replace("ControlTypeId", "")
        if s.endswith("ControlTypeId"):
            s = s[: -len("ControlTypeId")]
        return s or "Unknown"
    try:
        return str(int(raw))
    except Exception:  # noqa: BLE001
        return str(raw)


def _synthetic_aid(control_type: str, name: str, class_name: str, index: int) -> str:
    """Stable-ish synthetic automation_id when native id is empty (e.g. classic Notepad Edit)."""
    base = (name or class_name or control_type or "ctrl").strip().lower()
    base = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in base)[:48] or "ctrl"
    return f"__{control_type.lower()}_{base}_{index}"


# ---------------------------------------------------------------------------
# uiautomation path
# ---------------------------------------------------------------------------
def _tree_uiautomation(
    hwnd: int | None,
    *,
    max_depth: int,
    max_children: int,
) -> dict[str, Any]:
    import uiautomation as auto  # type: ignore

    if hwnd:
        root = auto.ControlFromHandle(int(hwnd))
    else:
        root = auto.GetRootControl()
    if root is None:
        raise RuntimeError("uiautomation: no root control")

    edit_index = 0

    def walk(ctrl: Any, depth: int) -> dict[str, Any]:
        nonlocal edit_index
        name = ""
        automation_id = ""
        class_name = ""
        control_type = "Unknown"
        value = None
        bounds = None
        state: dict[str, Any] = {}
        try:
            name = ctrl.Name or ""
        except Exception:  # noqa: BLE001
            pass
        try:
            automation_id = ctrl.AutomationId or ""
        except Exception:  # noqa: BLE001
            pass
        try:
            class_name = ctrl.ClassName or ""
        except Exception:  # noqa: BLE001
            pass
        try:
            ct = ctrl.ControlTypeName
            control_type = _control_type_name(ct)
        except Exception:  # noqa: BLE001
            try:
                control_type = _control_type_name(str(ctrl.ControlType))
            except Exception:  # noqa: BLE001
                pass
        try:
            rect = ctrl.BoundingRectangle
            if rect is not None:
                bounds = _bounds_dict(rect.left, rect.top, rect.right, rect.bottom)
        except Exception:  # noqa: BLE001
            pass
        try:
            # ValuePattern when available
            vp = ctrl.GetValuePattern()
            if vp:
                value = vp.Value
        except Exception:  # noqa: BLE001
            try:
                value = getattr(ctrl, "GetLegacyIAccessiblePattern", lambda: None)()
                if value is not None and hasattr(value, "Value"):
                    value = value.Value
            except Exception:  # noqa: BLE001
                value = None
        try:
            state = {
                "enabled": bool(getattr(ctrl, "IsEnabled", True)),
                "offscreen": bool(getattr(ctrl, "IsOffscreen", False)),
            }
        except Exception:  # noqa: BLE001
            state = {}

        if not automation_id and control_type in ("Edit", "Document", "Text"):
            automation_id = _synthetic_aid(control_type, name, class_name, edit_index)
            edit_index += 1

        node: dict[str, Any] = {
            "name": name,
            "control_type": control_type,
            "role": control_type,
            "automation_id": automation_id,
            "class_name": class_name,
            "value": value,
            "bounds": bounds,
            "state": state,
            "children": [],
            "source": "uiautomation",
        }
        if hwnd and depth == 0:
            node["hwnd"] = int(hwnd)

        if depth >= max_depth:
            return node
        children: list[Any] = []
        try:
            children = ctrl.GetChildren() or []
        except Exception:  # noqa: BLE001
            children = []
        for i, child in enumerate(children[:max_children]):
            try:
                node["children"].append(walk(child, depth + 1))
            except Exception:  # noqa: BLE001
                continue
        return node

    return walk(root, 0)


# ---------------------------------------------------------------------------
# comtypes + UIAutomationClient path
# ---------------------------------------------------------------------------
def _tree_comtypes(
    hwnd: int | None,
    *,
    max_depth: int,
    max_children: int,
) -> dict[str, Any]:
    import comtypes  # type: ignore
    import comtypes.client  # type: ignore

    # Load type library generated by comtypes
    UIAutomationClient = comtypes.client.GetModule("UIAutomationCore.dll")  # type: ignore
    CUIAutomation = UIAutomationClient.CUIAutomation
    TreeScope_Children = UIAutomationClient.TreeScope_Children
    UIA_NamePropertyId = UIAutomationClient.UIA_NamePropertyId
    UIA_AutomationIdPropertyId = UIAutomationClient.UIA_AutomationIdPropertyId
    UIA_ClassNamePropertyId = UIAutomationClient.UIA_ClassNamePropertyId
    UIA_ControlTypePropertyId = UIAutomationClient.UIA_ControlTypePropertyId
    UIA_BoundingRectanglePropertyId = UIAutomationClient.UIA_BoundingRectanglePropertyId
    UIA_ValueValuePropertyId = getattr(
        UIAutomationClient, "UIA_ValueValuePropertyId", None
    )
    UIA_IsEnabledPropertyId = getattr(UIAutomationClient, "UIA_IsEnabledPropertyId", None)
    UIA_IsOffscreenPropertyId = getattr(
        UIAutomationClient, "UIA_IsOffscreenPropertyId", None
    )

    # Control type id → name map (common subset)
    CT_MAP = {
        50000: "Button",
        50001: "Calendar",
        50002: "CheckBox",
        50003: "ComboBox",
        50004: "Edit",
        50005: "Hyperlink",
        50006: "Image",
        50007: "ListItem",
        50008: "List",
        50009: "Menu",
        50010: "MenuBar",
        50011: "MenuItem",
        50012: "ProgressBar",
        50013: "RadioButton",
        50014: "ScrollBar",
        50015: "Slider",
        50016: "Spinner",
        50017: "StatusBar",
        50018: "Tab",
        50019: "TabItem",
        50020: "Text",
        50021: "ToolBar",
        50022: "ToolTip",
        50023: "Tree",
        50024: "TreeItem",
        50025: "Custom",
        50026: "Group",
        50027: "Thumb",
        50028: "DataGrid",
        50029: "DataItem",
        50030: "Document",
        50031: "SplitButton",
        50032: "Window",
        50033: "Pane",
        50034: "Header",
        50035: "HeaderItem",
        50036: "Table",
        50037: "TitleBar",
        50038: "Separator",
    }

    automation = CUIAutomation()
    if hwnd:
        root = automation.ElementFromHandle(int(hwnd))
    else:
        root = automation.GetRootElement()
    if root is None:
        raise RuntimeError("comtypes UIA: no root element")

    edit_index = 0

    def prop(el: Any, pid: Any, default: Any = None) -> Any:
        if pid is None:
            return default
        try:
            return el.GetCurrentPropertyValue(pid)
        except Exception:  # noqa: BLE001
            return default

    def walk(el: Any, depth: int) -> dict[str, Any]:
        nonlocal edit_index
        name = prop(el, UIA_NamePropertyId, "") or ""
        automation_id = prop(el, UIA_AutomationIdPropertyId, "") or ""
        class_name = prop(el, UIA_ClassNamePropertyId, "") or ""
        ct_id = prop(el, UIA_ControlTypePropertyId, 0)
        try:
            control_type = CT_MAP.get(int(ct_id), f"ControlType_{ct_id}")
        except Exception:  # noqa: BLE001
            control_type = "Unknown"
        value = prop(el, UIA_ValueValuePropertyId, None) if UIA_ValueValuePropertyId else None
        rect = prop(el, UIA_BoundingRectanglePropertyId, None)
        bounds = None
        if rect is not None:
            try:
                # VARIANT may be list/tuple of 4 doubles
                vals = list(rect)
                if len(vals) >= 4:
                    left, top, width, height = vals[0], vals[1], vals[2], vals[3]
                    bounds = _bounds_dict(left, top, left + width, top + height)
            except Exception:  # noqa: BLE001
                bounds = None
        state = {
            "enabled": bool(prop(el, UIA_IsEnabledPropertyId, True)),
            "offscreen": bool(prop(el, UIA_IsOffscreenPropertyId, False)),
        }
        if not automation_id and control_type in ("Edit", "Document", "Text"):
            automation_id = _synthetic_aid(control_type, name, class_name, edit_index)
            edit_index += 1

        node: dict[str, Any] = {
            "name": name,
            "control_type": control_type,
            "role": control_type,
            "automation_id": automation_id,
            "class_name": class_name,
            "value": value,
            "bounds": bounds,
            "state": state,
            "children": [],
            "source": "comtypes",
        }
        if hwnd and depth == 0:
            node["hwnd"] = int(hwnd)

        if depth >= max_depth:
            return node
        try:
            condition = automation.CreateTrueCondition()
            found = el.FindAll(TreeScope_Children, condition)
            count = int(found.Length) if found is not None else 0
        except Exception:  # noqa: BLE001
            count = 0
            found = None
        for i in range(min(count, max_children)):
            try:
                child = found.GetElement(i)
                node["children"].append(walk(child, depth + 1))
            except Exception:  # noqa: BLE001
                continue
        return node

    return walk(root, 0)


# ---------------------------------------------------------------------------
# pywinauto path
# ---------------------------------------------------------------------------
def _tree_pywinauto(
    hwnd: int | None,
    *,
    max_depth: int,
    max_children: int,
) -> dict[str, Any]:
    from pywinauto import Desktop  # type: ignore
    from pywinauto.uia_element_info import UIAElementInfo  # type: ignore

    if hwnd:
        info = UIAElementInfo(int(hwnd))
        root_wrapper = Desktop(backend="uia").window(handle=int(hwnd))
        # Prefer element_info walking via wrapper
        root = root_wrapper
    else:
        root = Desktop(backend="uia")

    edit_index = 0

    def walk_wrapper(w: Any, depth: int) -> dict[str, Any]:
        nonlocal edit_index
        try:
            ei = w.element_info
        except Exception:  # noqa: BLE001
            ei = w
        name = getattr(ei, "name", None) or ""
        automation_id = getattr(ei, "automation_id", None) or ""
        class_name = getattr(ei, "class_name", None) or ""
        control_type = _control_type_name(getattr(ei, "control_type", None) or "Unknown")
        value = None
        try:
            if hasattr(w, "get_value"):
                value = w.get_value()
            elif hasattr(w, "iface_value"):
                value = w.iface_value.CurrentValue
        except Exception:  # noqa: BLE001
            value = None
        bounds = None
        try:
            r = getattr(ei, "rectangle", None)
            if r is not None:
                bounds = _bounds_dict(r.left, r.top, r.right, r.bottom)
        except Exception:  # noqa: BLE001
            pass
        state: dict[str, Any] = {}
        try:
            state["enabled"] = bool(getattr(ei, "enabled", True))
            state["visible"] = bool(getattr(ei, "visible", True))
        except Exception:  # noqa: BLE001
            pass

        if not automation_id and control_type in ("Edit", "Document", "Text"):
            automation_id = _synthetic_aid(control_type, name, class_name, edit_index)
            edit_index += 1

        node: dict[str, Any] = {
            "name": name,
            "control_type": control_type,
            "role": control_type,
            "automation_id": automation_id,
            "class_name": class_name,
            "value": value,
            "bounds": bounds,
            "state": state,
            "children": [],
            "source": "pywinauto",
        }
        if hwnd and depth == 0:
            node["hwnd"] = int(hwnd)

        if depth >= max_depth:
            return node
        try:
            children = w.children()
        except Exception:  # noqa: BLE001
            children = []
        for child in list(children)[:max_children]:
            try:
                node["children"].append(walk_wrapper(child, depth + 1))
            except Exception:  # noqa: BLE001
                continue
        return node

    return walk_wrapper(root, 0)


def build_tree(
    hwnd: int | None = None,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_children: int = DEFAULT_MAX_CHILDREN,
) -> dict[str, Any]:
    """Build a rich UIA tree. Prefer uiautomation → comtypes → pywinauto."""
    if sys.platform != "win32":
        raise RuntimeError("UIA tree requires Windows (win32)")

    errors: list[str] = []

    try:
        import uiautomation  # noqa: F401

        return _tree_uiautomation(hwnd, max_depth=max_depth, max_children=max_children)
    except ImportError:
        errors.append("uiautomation not installed")
    except Exception as e:  # noqa: BLE001
        errors.append(f"uiautomation: {e}")

    try:
        import comtypes  # noqa: F401

        return _tree_comtypes(hwnd, max_depth=max_depth, max_children=max_children)
    except ImportError:
        errors.append("comtypes not installed")
    except Exception as e:  # noqa: BLE001
        errors.append(f"comtypes: {e}")

    try:
        import pywinauto  # noqa: F401

        return _tree_pywinauto(hwnd, max_depth=max_depth, max_children=max_children)
    except ImportError:
        errors.append("pywinauto not installed")
    except Exception as e:  # noqa: BLE001
        errors.append(f"pywinauto: {e}")

    raise RuntimeError(
        "No working UIA backend (need uiautomation, comtypes, or pywinauto): "
        + "; ".join(errors)
    )


def find_by_automation_id(tree: dict[str, Any], automation_id: str) -> dict[str, Any] | None:
    if tree.get("automation_id") == automation_id:
        return tree
    for child in tree.get("children") or []:
        found = find_by_automation_id(child, automation_id)
        if found:
            return found
    return None


def find_by_name(tree: dict[str, Any], name: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    if tree.get("name") == name:
        hits.append(tree)
    for child in tree.get("children") or []:
        hits.extend(find_by_name(child, name))
    return hits


def find_first(
    tree: dict[str, Any],
    *,
    automation_id: str | None = None,
    name: str | None = None,
    control_type: str | None = None,
) -> dict[str, Any] | None:
    """Depth-first find by optional automation_id / name / control_type filters."""

    def match(node: dict[str, Any]) -> bool:
        if automation_id is not None and node.get("automation_id") != automation_id:
            return False
        if name is not None and node.get("name") != name:
            return False
        if control_type is not None:
            ct = (node.get("control_type") or node.get("role") or "").lower()
            if control_type.lower() not in ct:
                return False
        return True

    stack = [tree]
    while stack:
        node = stack.pop(0)
        if match(node):
            return node
        stack[0:0] = list(node.get("children") or [])
    return None


def _click_bounds(bounds: dict[str, Any] | None) -> dict[str, Any]:
    if not bounds:
        return {"ok": False, "error": "no bounds for click"}
    x = int(bounds["left"] + max(1, bounds.get("width", 1)) // 2)
    y = int(bounds["top"] + max(1, bounds.get("height", 1)) // 2)
    # Delegate to WindowsBackend SendInput path when available
    try:
        from windows_os_api.backends.windows import WindowsBackend

        backend = WindowsBackend()
        return backend.mouse_click(x, y, "left")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "x": x, "y": y}


def invoke_click(node: dict[str, Any]) -> dict[str, Any]:
    """Invoke click on a control node (UIA Invoke when possible, else center click)."""
    if sys.platform != "win32":
        return {"ok": False, "error": "invoke_click requires Windows"}

    # Try uiautomation Invoke / Click
    try:
        import uiautomation as auto  # type: ignore

        aid = node.get("automation_id") or ""
        name = node.get("name") or ""
        ctrl = None
        if aid and not str(aid).startswith("__"):
            ctrl = auto.Control(searchDepth=12, AutomationId=aid)
            if not ctrl.Exists(0, 0):
                ctrl = None
        if ctrl is None and name:
            ctrl = auto.Control(searchDepth=12, Name=name)
            if not ctrl.Exists(0, 0):
                ctrl = None
        if ctrl is not None and ctrl.Exists(1, 0.5):
            try:
                ctrl.GetInvokePattern().Invoke()
                return {"ok": True, "method": "InvokePattern", "name": name, "automation_id": aid}
            except Exception:  # noqa: BLE001
                ctrl.Click(simulateMove=False)
                return {"ok": True, "method": "uiautomation.Click", "name": name, "automation_id": aid}
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        # fall through to bounds click
        _ = e

    return _click_bounds(node.get("bounds"))


def set_value(node: dict[str, Any], value: str) -> dict[str, Any]:
    """Set value on an Edit/Document control via UIA ValuePattern or type_text."""
    if sys.platform != "win32":
        return {"ok": False, "error": "set_value requires Windows"}

    aid = node.get("automation_id") or ""
    name = node.get("name") or ""

    try:
        import uiautomation as auto  # type: ignore

        ctrl = None
        if aid and not str(aid).startswith("__"):
            ctrl = auto.EditControl(searchDepth=12, AutomationId=aid)
            if not ctrl.Exists(0, 0):
                ctrl = auto.Control(searchDepth=12, AutomationId=aid)
        if (ctrl is None or not ctrl.Exists(0, 0)) and name:
            ctrl = auto.EditControl(searchDepth=12, Name=name)
        if ctrl is not None and ctrl.Exists(1, 0.5):
            try:
                ctrl.GetValuePattern().SetValue(value)
                return {"ok": True, "method": "ValuePattern", "value": value, "automation_id": aid}
            except Exception:  # noqa: BLE001
                try:
                    ctrl.Click()
                except Exception:  # noqa: BLE001
                    pass
                # Fall through to SendInput typing
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        pass

    # Bounds focus + type
    bounds = node.get("bounds")
    if bounds:
        _click_bounds(bounds)
    try:
        from windows_os_api.backends.windows import WindowsBackend

        backend = WindowsBackend()
        # Select-all then type for replace semantics
        backend.key_press("a", modifiers=["ctrl"])
        typed = backend.type_text(value)
        return {
            "ok": bool(typed.get("ok")),
            "method": "SendInput",
            "value": value,
            "automation_id": aid,
            "typed": typed,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "value": value}


def get_notepad_tree(*, max_depth: int = DEFAULT_MAX_DEPTH) -> dict[str, Any]:
    """Best-effort UI tree for a running Notepad window."""
    if sys.platform != "win32":
        raise RuntimeError("UIA notepad tree requires Windows")
    if not uia_available():
        raise RuntimeError("No UIA library (uiautomation/comtypes/pywinauto)")

    # Prefer handle lookup via win32gui
    hwnd = None
    try:
        import win32gui  # type: ignore

        def _enum(h, acc):
            if win32gui.IsWindowVisible(h):
                title = win32gui.GetWindowText(h) or ""
                cls = win32gui.GetClassName(h) or ""
                if cls == "Notepad" or "Notepad" in title:
                    acc.append(h)

        found: list[int] = []
        win32gui.EnumWindows(_enum, found)
        if found:
            hwnd = found[0]
    except Exception:  # noqa: BLE001
        hwnd = None

    if hwnd is not None:
        tree = build_tree(hwnd, max_depth=max_depth)
        tree["hwnd"] = int(hwnd)
        return tree

    # Fallback: search by class via uiautomation
    try:
        import uiautomation as auto  # type: ignore

        win = auto.WindowControl(searchDepth=1, ClassName="Notepad")
        if not win.Exists(0, 0):
            win = auto.WindowControl(searchDepth=1, Name="Untitled - Notepad")
        if not win.Exists(1, 0.5):
            raise RuntimeError("Notepad window not found")
        handle = win.NativeWindowHandle
        return build_tree(int(handle) if handle else None, max_depth=max_depth)
    except ImportError:
        pass
    except RuntimeError:
        raise
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Notepad window not found: {e}") from e

    try:
        from pywinauto import Desktop  # type: ignore

        desk = Desktop(backend="uia")
        for w in desk.windows():
            title = w.window_text()
            if "Notepad" in title or w.class_name() == "Notepad":
                handle = w.handle
                return build_tree(int(handle), max_depth=max_depth)
        raise RuntimeError("Notepad window not found via pywinauto")
    except ImportError as e:
        raise RuntimeError("UIA libraries present but notepad tree failed") from e
