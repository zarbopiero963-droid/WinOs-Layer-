"""Self-healing — recover from stale UI selectors."""
from __future__ import annotations
from typing import Any
from windows_os_api.apps.ui_inspector.service import get_tree, find_by_automation_id, find_by_name, flatten

def heal_selector(hwnd: int, automation_id: str, fallback_name: str | None = None) -> dict[str, Any]:
    tree = get_tree(hwnd)
    node = find_by_automation_id(tree, automation_id)
    if node:
        return {"ok": True, "healed": False, "element": node}
    # Try by name
    if fallback_name:
        hits = find_by_name(tree, fallback_name)
        if hits:
            return {"ok": True, "healed": True, "strategy": "name", "element": hits[0]}
    # Try fuzzy on automation_id suffix
    suffix = automation_id.split(".")[-1]
    for n in flatten(tree):
        aid = n.get("automation_id") or ""
        if suffix and suffix in aid:
            return {"ok": True, "healed": True, "strategy": "fuzzy_id", "element": n}
    return {"ok": False, "healed": False, "error": "element not recoverable"}
