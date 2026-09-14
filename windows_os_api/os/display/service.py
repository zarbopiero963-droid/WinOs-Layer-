"""Displays and screenshots."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend
from windows_os_api.os.capability import discover

def list_displays() -> dict[str, Any]:
    """Display, col contratto `supported` (D3 / N004).

    Lista bare → vuoto indistinguibile da unsupported. Chiave `displays`
    invariata (additivo).
    """
    b = get_backend()
    return discover(b, "displays", "displays", b.list_displays)

def screenshot(display_id: int | None = None) -> dict[str, Any]:
    return get_backend().screenshot(display_id)
