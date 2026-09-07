"""Windows services control."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend

def list_services() -> list[dict[str, Any]]:
    return get_backend().list_services()

def control(name: str, action: str) -> dict[str, Any]:
    return get_backend().control_service(name, action)
