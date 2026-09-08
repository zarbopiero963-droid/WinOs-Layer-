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

def close_window(hwnd: int, timeout: float = 5.0) -> dict[str, Any]:
    return get_backend().close_window(hwnd, timeout)

def active_window() -> int | None:
    return get_backend().active_window()


# Geometry / state. Validation lives in os/windows/geometry.py and is applied by
# each backend at the point that acts, so a caller reaching a backend directly
# gets the same answer as one coming through here.
def move_window(hwnd: int, x: int, y: int) -> dict[str, Any]:
    return get_backend().move_window(hwnd, x, y)

def resize_window(hwnd: int, width: int, height: int) -> dict[str, Any]:
    return get_backend().resize_window(hwnd, width, height)

def minimize_window(hwnd: int) -> dict[str, Any]:
    return get_backend().minimize_window(hwnd)

def maximize_window(hwnd: int) -> dict[str, Any]:
    return get_backend().maximize_window(hwnd)

def restore_window(hwnd: int) -> dict[str, Any]:
    return get_backend().restore_window(hwnd)
