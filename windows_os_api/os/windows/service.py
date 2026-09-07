"""Window manager."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend

def list_windows() -> list[dict[str, Any]]:
    return get_backend().list_windows()

def get_window(hwnd: int) -> dict[str, Any] | None:
    return get_backend().get_window(hwnd)

def focus_window(hwnd: int) -> dict[str, Any]:
    return get_backend().focus_window(hwnd)

def close_window(hwnd: int) -> dict[str, Any]:
    return get_backend().close_window(hwnd)
