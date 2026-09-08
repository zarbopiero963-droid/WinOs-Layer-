"""Action discovery from UI trees and adapters."""
from __future__ import annotations
from typing import Any
from windows_os_api.apps.adapters.engine import create_adapter, get_adapter, list_adapters
from windows_os_api.apps.ui_inspector.service import get_tree, flatten

def discover_actions(app_id: str, hwnd: int = 1001) -> list[dict[str, Any]]:
    adapter = get_adapter(app_id) or create_adapter(app_id, hwnd)
    return [
        {"name": a.name, "description": a.description, "automation_id": a.automation_id,
         "control_type": a.control_type, "params": a.params, "risk": a.risk}
        for a in adapter.actions
    ]

def catalog() -> list[dict[str, Any]]:
    return list_adapters()
