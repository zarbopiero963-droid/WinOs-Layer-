"""Windows/Linux services control."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend
from windows_os_api.os.capability import discover

def list_services() -> dict[str, Any]:
    """Servizi, col contratto `supported` (decisione owner D3-A).

    La chiave `services` resta dov'era: chi legge solo quella non si accorge
    del cambiamento. Chi ha bisogno di distinguere «nessun servizio» da «non
    so enumerarli» ora puo'.
    """
    b = get_backend()
    return discover(b, "services", "services", b.list_services)

def control(name: str, action: str, scope: str = "user") -> dict[str, Any]:
    b = get_backend()
    # LinuxBackend accetta scope; Fake/Windows no
    try:
        return b.control_service(name, action, scope=scope)  # type: ignore[call-arg]
    except TypeError:
        return b.control_service(name, action)
