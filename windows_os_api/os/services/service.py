"""Windows/Linux services control."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend

def list_services() -> list[dict[str, Any]]:
    return get_backend().list_services()

def control(name: str, action: str, scope: str = "user") -> dict[str, Any]:
    b = get_backend()
    # LinuxBackend accepts scope; Fake/Windows may not
    try:
        return b.control_service(name, action, scope=scope)  # type: ignore[call-arg]
    except TypeError:
        return b.control_service(name, action)
