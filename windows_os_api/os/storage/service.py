"""Storage / drives."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend
from windows_os_api.os.capability import discover

def list_drives() -> dict[str, Any]:
    """Volumi/dischi, col contratto `supported` (D3 / N004).

    Lista bare → vuoto indistinguibile da unsupported. Chiave `drives`
    invariata (additivo).
    """
    b = get_backend()
    return discover(b, "drives", "drives", b.list_drives)
