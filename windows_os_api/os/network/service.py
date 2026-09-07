"""Network interfaces / connections."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend

def interfaces() -> list[dict[str, Any]]:
    return get_backend().network_interfaces()

def connections() -> list[dict[str, Any]]:
    return get_backend().network_connections()
