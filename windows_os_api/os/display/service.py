"""Displays and screenshots."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend

def list_displays() -> list[dict[str, Any]]:
    return get_backend().list_displays()

def screenshot(display_id: int | None = None) -> dict[str, Any]:
    return get_backend().screenshot(display_id)
