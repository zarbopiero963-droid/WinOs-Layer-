"""UI Automation tree model + inspector (Windows UIA / Linux AT-SPI / Fake)."""
from __future__ import annotations

from typing import Any

from windows_os_api.apps.sandbox.ui_guard import check_ui_target
from windows_os_api.apps.sandbox.ui_guard import rejection as ui_rejection
from windows_os_api.backends.factory import get_backend


def get_tree(hwnd: int | None = None) -> dict[str, Any]:
    return get_backend().get_ui_tree(hwnd)


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


def flatten(tree: dict[str, Any], path: str = "") -> list[dict[str, Any]]:
    node_path = f"{path}/{tree.get('name', '')}" if path else tree.get("name", "")
    rows = [
        {
            "path": node_path,
            "name": tree.get("name"),
            "control_type": tree.get("control_type"),
            "role": tree.get("role"),
            "automation_id": tree.get("automation_id"),
            "value": tree.get("value"),
            "states": tree.get("states"),
            "bounds": tree.get("bounds"),
        }
    ]
    for child in tree.get("children") or []:
        rows.extend(flatten(child, node_path))
    return rows


def find_accessible(
    name: str | None = None,
    role: str | None = None,
    *,
    exact: bool = False,
) -> dict[str, Any] | None:
    backend = get_backend()
    if hasattr(backend, "find_accessible"):
        return backend.find_accessible(name=name, role=role, exact=exact)
    # Fallback: walk get_ui_tree
    tree = backend.get_ui_tree()
    stack = list(tree.get("children") or [])
    while stack:
        node = stack.pop(0)
        n = node.get("name") or ""
        r = (node.get("role") or node.get("control_type") or "").lower()
        ok_name = True
        ok_role = True
        if name is not None:
            ok_name = (n == name) if exact else (name.lower() in n.lower())
        if role is not None:
            ok_role = role.lower() in r
        if ok_name and ok_role:
            return node
        stack[0:0] = list(node.get("children") or [])
    return None


def find_text_vision(text: str) -> dict[str, Any]:
    """OCR/vision find — used when AT-SPI tree is empty/unsupported."""
    from windows_os_api.apps.vision.ocr import find_text_on_screen

    return find_text_on_screen(text)


def click_text_vision(text: str, *, dry_run: bool = False) -> dict[str, Any]:
    # La policy sandbox vale anche qui. Senza questo controllo bastava chiedere
    # il testo che si legge SOPRA il pulsante per premere un'azione negata: il
    # gate stava in `invoke_action`, e questa strada non ci passa. La misura del
    # difetto e il ragionamento per esteso stanno in `sandbox/ui_guard.py`.
    verdict = check_ui_target(text, kind="click_text")
    if not verdict["allowed"]:
        return ui_rejection(verdict, text)

    from windows_os_api.apps.vision.ocr import click_text

    return click_text(text, dry_run=dry_run)


def accessible_click(name: str, role: str | None = None) -> dict[str, Any]:
    verdict = check_ui_target(name, kind="click")
    if not verdict["allowed"]:
        return ui_rejection(verdict, name)

    backend = get_backend()
    result: dict[str, Any] | None = None
    if hasattr(backend, "accessible_click"):
        result = backend.accessible_click(name, role=role)
        if result.get("ok"):
            return result
    # Vision fallback for canvas / games / custom UI without AT-SPI
    vision = click_text_vision(name, dry_run=False)
    if vision.get("ok"):
        return {"ok": True, "fallback": "vision", "vision": vision, "name": name, "atspi": result}
    return {
        "ok": False,
        "error": "accessible_click not supported or element not found",
        "backend": backend.name,
        "atspi": result,
        "vision": vision,
    }


def accessible_set_text(name: str, text: str, role: str | None = None) -> dict[str, Any]:
    verdict = check_ui_target(name, kind="set_text")
    if not verdict["allowed"]:
        return ui_rejection(verdict, name)

    backend = get_backend()
    if hasattr(backend, "accessible_set_text"):
        return backend.accessible_set_text(name, text, role=role)
    return {
        "ok": False,
        "error": "accessible_set_text not supported by backend",
        "backend": backend.name,
    }
