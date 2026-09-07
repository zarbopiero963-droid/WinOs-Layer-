"""UI Automation tree model + inspector."""
from __future__ import annotations
from typing import Any
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
    rows = [{"path": node_path, "name": tree.get("name"), "control_type": tree.get("control_type"),
             "automation_id": tree.get("automation_id"), "value": tree.get("value")}]
    for child in tree.get("children") or []:
        rows.extend(flatten(child, node_path))
    return rows
