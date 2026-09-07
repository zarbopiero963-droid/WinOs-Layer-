"""System info / resources / uptime / power."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend

def system_info() -> dict[str, Any]:
    return get_backend().get_system_info()

def resources() -> dict[str, Any]:
    return get_backend().get_resources()

def uptime() -> dict[str, Any]:
    return get_backend().get_uptime()

def power(action: str) -> dict[str, Any]:
    return get_backend().power_action(action)

def capabilities() -> dict[str, Any]:
    b = get_backend()
    return {
        "backend": b.name,
        "features": [
            "system", "processes", "apps", "windows", "ui", "input", "clipboard",
            "display", "filesystem", "storage", "network", "services", "audio",
            "devices", "printers", "users", "registry", "terminal", "adapters",
            "workflows", "mcp", "websocket", "metrics",
        ],
        "permissions": [
            "system.read", "filesystem.read", "filesystem.write", "process.read",
            "process.execute", "ui.read", "ui.control", "network.read",
            "service.control", "admin",
        ],
    }
