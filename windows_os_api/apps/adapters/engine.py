"""Universal Adapter engine — turns EXE UI into virtual API actions."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable
from windows_os_api.apps.ui_inspector.service import find_by_automation_id, get_tree
from windows_os_api.backends.factory import get_backend

@dataclass
class AdapterAction:
    name: str
    description: str
    automation_id: str
    control_type: str
    params: list[str] = field(default_factory=list)
    risk: str = "low"

@dataclass
class Adapter:
    app_id: str
    app_name: str
    hwnd: int
    actions: list[AdapterAction] = field(default_factory=list)
    trust_level: str = "unsigned"
    openapi: dict[str, Any] = field(default_factory=dict)

_adapters: dict[str, Adapter] = {}

def _default_actions_from_tree(tree: dict[str, Any]) -> list[AdapterAction]:
    actions: list[AdapterAction] = []
    def walk(node: dict[str, Any]) -> None:
        ct = node.get("control_type")
        aid = node.get("automation_id") or ""
        name = node.get("name") or aid or "unknown"
        if ct == "Button" and aid:
            actions.append(AdapterAction(
                name=f"click_{aid.replace('.', '_')}",
                description=f"Click button {name}",
                automation_id=aid,
                control_type=ct,
                risk="medium" if "delete" in name.lower() or "exit" in name.lower() else "low",
            ))
        if ct == "Edit" and aid:
            actions.append(AdapterAction(
                name=f"set_{aid.replace('.', '_')}",
                description=f"Set field {name}",
                automation_id=aid,
                control_type=ct,
                params=["value"],
                risk="low",
            ))
        if ct == "MenuItem" and aid and not node.get("children"):
            actions.append(AdapterAction(
                name=f"menu_{aid.replace('.', '_')}",
                description=f"Invoke menu {name}",
                automation_id=aid,
                control_type=ct,
                risk="medium",
            ))
        for c in node.get("children") or []:
            walk(c)
    walk(tree)
    return actions

def create_adapter(app_id: str, hwnd: int = 1001, trust_level: str = "unsigned") -> Adapter:
    tree = get_tree(hwnd)
    actions = _default_actions_from_tree(tree)
    adapter = Adapter(
        app_id=app_id,
        app_name=tree.get("name") or app_id,
        hwnd=hwnd,
        actions=actions,
        trust_level=trust_level,
    )
    adapter.openapi = generate_adapter_openapi(adapter)
    _adapters[app_id] = adapter
    return adapter

def get_adapter(app_id: str) -> Adapter | None:
    return _adapters.get(app_id)

def list_adapters() -> list[dict[str, Any]]:
    return [
        {"app_id": a.app_id, "app_name": a.app_name, "hwnd": a.hwnd,
         "actions": len(a.actions), "trust_level": a.trust_level}
        for a in _adapters.values()
    ]

def invoke_action(app_id: str, action_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    adapter = _adapters.get(app_id)
    if not adapter:
        return {"ok": False, "error": "adapter not found"}
    action = next((a for a in adapter.actions if a.name == action_name), None)
    if not action:
        return {"ok": False, "error": f"action not found: {action_name}"}
    params = params or {}
    # Permission / sandbox check delegated to caller; perform UI action via backend
    backend = get_backend()
    tree = backend.get_ui_tree(adapter.hwnd)
    node = find_by_automation_id(tree, action.automation_id)
    if not node:
        return {"ok": False, "error": "UI element not found", "automation_id": action.automation_id}
    if action.control_type == "Edit":
        value = params.get("value", "")
        node["value"] = value
        backend.type_text(str(value))
        return {"ok": True, "action": action_name, "set_value": value, "element": action.automation_id}
    if action.control_type in ("Button", "MenuItem"):
        backend.mouse_click(10, 10)
        return {"ok": True, "action": action_name, "clicked": action.automation_id}
    return {"ok": True, "action": action_name, "element": action.automation_id}

def generate_adapter_openapi(adapter: Adapter) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for a in adapter.actions:
        path = f"/v1/apps/{adapter.app_id}/actions/{a.name}"
        props = {p: {"type": "string"} for p in a.params}
        paths[path] = {
            "post": {
                "summary": a.description,
                "operationId": a.name,
                "requestBody": {
                    "content": {"application/json": {"schema": {"type": "object", "properties": props}}}
                } if a.params else None,
                "responses": {"200": {"description": "OK"}},
                "x-risk": a.risk,
            }
        }
    return {
        "openapi": "3.0.3",
        "info": {"title": f"{adapter.app_name} Virtual API", "version": "1.0.0"},
        "paths": paths,
    }

def reset_adapters() -> None:
    _adapters.clear()
