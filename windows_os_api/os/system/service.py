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
        # Il backend fake non ha `capability_flags`: la sua tabella e' qui, ed e'
        # legittimamente una fixture — supporta tutto perche' tutto e' simulato.
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
            "devices": True,
            "printers": True,
            "registry_compat": True,
            "windows_uia": False,
            "fake_crm": True,
            "ocr": True,
            "ocr_tesseract": False,
            "wayland": False,
            "x11": False,
            "privileged": False,
            "vision": True,
        }
    else:
        flags = {"system": True}

    # NOTA — qui c'era una terza tabella, `elif backend == "windows"`, hardcoded.
    # Era IRRAGGIUNGIBILE: `WindowsBackend` espone `capability_flags`, quindi il
    # primo ramo la intercetta sempre. E dichiarava `"services": False` — falso
    # da quando il backend enumera davvero i servizi via il Service Control
    # Manager. Una tabella morta che dice il falso e' peggio di nessuna tabella:
    # nessuno la corregge, perche' nessuno la vede sbagliare. Un test verifica
    # che non torni (`test_capability_flags_contract.py`).

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
                "LinuxBackend adapters fall back to vision/OCR when AT-SPI tree is empty."
            ),
            "wayland": "Compositor-specific tools (ydotool/wtype, wlrctl/swaymsg/hyprctl); portals limited",
            "ocr": "tesseract preferred; Pillow template fallback always available",
            "privileged": "Requires ADMIN + WINOS_ALLOW_PRIVILEGED=true; never silent root",
        },
    }
