"""Printers."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend
from windows_os_api.os.capability import discover

def list_printers() -> dict[str, Any]:
    """Stampanti, col contratto `supported` (decisione owner D3-A)."""
    b = get_backend()
    return discover(b, "printers", "printers", b.list_printers)
