"""Windows/Linux services control."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend
from windows_os_api.os.capability import discover
from windows_os_api.os.services.allowlist import (
    ServiceRejected,
    check as check_service,
    rejection,
)

def list_services() -> dict[str, Any]:
    """Servizi, col contratto `supported` (decisione owner D3-A).

    La chiave `services` resta dov'era: chi legge solo quella non si accorge
    del cambiamento. Chi ha bisogno di distinguere «nessun servizio» da «non
    so enumerarli» ora puo'.
    """
    b = get_backend()
    return discover(b, "services", "services", b.list_services)

def control(name: str, action: str, scope: str = "user") -> dict[str, Any]:
    """Controlla un servizio — solo se l'allowlist lo autorizza (decisione D1-B).

    Il controllo sta PRIMA di qualunque cosa raggiunga il backend: un servizio
    non autorizzato non arriva mai a `systemctl`, non «fallisce dopo averci
    provato». E sta qui, non nella route, perche' questo e' il punto che ogni
    superficie attraversa — un controllo che il chiamante puo' dimenticare non
    e' un controllo (#13).

    Il ruolo del chiamante non entra: ADMIN non e' una scorciatoia attorno
    all'allowlist, per decisione esplicita dell'owner.
    """
    try:
        canonical = check_service(name, action)
    except ServiceRejected as exc:
        return rejection(exc, name, action)

    b = get_backend()
    # LinuxBackend accetta scope; Fake/Windows no
    try:
        return b.control_service(canonical, action, scope=scope)  # type: ignore[call-arg]
    except TypeError:
        return b.control_service(canonical, action)
