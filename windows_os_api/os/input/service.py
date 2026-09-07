"""Mouse / keyboard."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend

def mouse_move(x: int, y: int) -> dict[str, Any]:
    return get_backend().mouse_move(x, y)

def mouse_click(x: int, y: int, button: str = "left") -> dict[str, Any]:
    return get_backend().mouse_click(x, y, button)

def key_press(key: str, modifiers: list[str] | None = None) -> dict[str, Any]:
    return get_backend().key_press(key, modifiers)

def type_text(text: str) -> dict[str, Any]:
    return get_backend().type_text(text)

def clipboard_get() -> dict[str, Any]:
    return get_backend().clipboard_get()

def clipboard_set(text: str) -> dict[str, Any]:
    return get_backend().clipboard_set(text)
