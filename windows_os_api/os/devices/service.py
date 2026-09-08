"""Device enumeration."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend
from windows_os_api.os.capability import discover

def list_devices() -> dict[str, Any]:
    """Dispositivi a blocchi, col contratto `supported` (decisione owner D3-A).

    Restituisce l'inviluppo completo, non la sola lista: la chiave `devices`
    resta dov'era — additivo — e accanto compare cio' che prima mancava, cioe'
    se la lista vuota significhi «nessuno» o «non ho potuto guardare».
    """
    b = get_backend()
    return discover(b, "devices", "devices", b.list_devices)
