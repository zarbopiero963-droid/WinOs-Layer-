"""System info / resources / uptime / power / capabilities."""
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
    flags: dict[str, bool]
    if hasattr(b, "capability_flags"):
        flags = dict(b.capability_flags())
    elif getattr(b, "name", "") == "fake":
        flags = {
            "processes": True,
            "filesystem": True,
            "network": True,
            "system": True,
            "windows_ui": True,
            "atspi": False,
            "clipboard": True,
            "screenshot": True,
            "audio": True,
            "services": True,
            "registry_compat": True,
            "windows_uia": False,
            "fake_crm": True,
        }
    elif getattr(b, "name", "") == "windows":
        flags = {
            "processes": True,
            "filesystem": True,
            "network": True,
            "system": True,
            "windows_ui": True,
            "atspi": False,
            "clipboard": True,
            "screenshot": False,
            "audio": False,
            "services": False,
            "registry_compat": False,
            "windows_uia": True,  # UIA path exists on Windows (may still be stubbed)
            "fake_crm": False,
        }
    else:
        flags = {"system": True}

    feature_list = [
        "system",
        "processes",
        "apps",
        "windows",
        "ui",
        "input",
        "clipboard",
        "display",
        "filesystem",
        "storage",
        "network",
        "services",
        "audio",
        "devices",
        "printers",
        "users",
        "registry",
        "terminal",
        "adapters",
        "workflows",
        "mcp",
        "websocket",
        "metrics",
    ]
    # Honest: drop UI/window features when flags say unavailable
    if flags.get("windows_ui") is False:
        # still expose the API surface, but flag honesty in feature_flags
        pass

    return {
        "backend": b.name,
        "features": feature_list,
        "feature_flags": flags,
        "permissions": [
            "system.read",
            "filesystem.read",
            "filesystem.write",
            "process.read",
            "process.execute",
            "ui.read",
            "ui.control",
            "network.read",
            "service.control",
            "admin",
        ],
        "notes": {
            "windows_uia_on_linux": False,
            "adapter_ui": (
                "Use WINOS_BACKEND=fake for Contoso CRM UI-tree demos; "
                "LinuxBackend adapters may have empty actions until AT-SPI is available."
            ),
        },
    }
