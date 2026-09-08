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

# Validation lives in os/input/validation.py and is applied by each backend at
# the point that acts, so a caller reaching a backend directly gets the same
# answer as one coming through here.
def double_click(x: int, y: int, button: str = "left") -> dict[str, Any]:
    return get_backend().double_click(x, y, button)

def scroll(direction: str = "down", amount: int = 3,
           x: int | None = None, y: int | None = None) -> dict[str, Any]:
    return get_backend().scroll(direction, amount, x, y)

def key_down(key: str) -> dict[str, Any]:
    return get_backend().key_down(key)

def key_up(key: str) -> dict[str, Any]:
    return get_backend().key_up(key)

def hotkey(keys: list[str]) -> dict[str, Any]:
    return get_backend().hotkey(keys)

def mouse_drag(x1: int, y1: int, x2: int, y2: int,
               button: str = "left", steps: int = 10) -> dict[str, Any]:
    return get_backend().mouse_drag(x1, y1, x2, y2, button, steps=steps)

def pointer_position() -> dict[str, int] | None:
    return get_backend().pointer_position()

def clipboard_get() -> dict[str, Any]:
    return get_backend().clipboard_get()

def clipboard_set(text: str) -> dict[str, Any]:
    return get_backend().clipboard_set(text)
