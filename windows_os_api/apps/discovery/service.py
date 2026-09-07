"""Application discovery."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend

_registry: dict[str, dict[str, Any]] = {}

def discover() -> list[dict[str, Any]]:
    apps = get_backend().discover_apps()
    for a in apps:
        _registry[a["id"]] = a
    return apps

def get_app(app_id: str) -> dict[str, Any] | None:
    if not _registry:
        discover()
    return _registry.get(app_id)

def registry() -> list[dict[str, Any]]:
    if not _registry:
        discover()
    return list(_registry.values())

def register_app(app: dict[str, Any]) -> dict[str, Any]:
    aid = app.get("id") or app["name"].lower().replace(" ", "-")
    app = {**app, "id": aid}
    _registry[aid] = app
    return app
