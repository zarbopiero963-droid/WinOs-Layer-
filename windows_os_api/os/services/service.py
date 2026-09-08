"""Windows/Linux services control."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend
from windows_os_api.os.capability import discover, unsupported
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
    b = get_backend()

    # La capability PRIMA dell'allowlist, di proposito (decisione owner D5-B).
    #
    # Se il backend non implementa il controllo dei servizi, «non e' in
    # allowlist» sarebbe una risposta fuorviante: suggerirebbe che aggiungendolo
    # funzionerebbe, e non e' vero. Il motivo piu' fondamentale va detto per
    # primo. L'allowlist resta il confine per tutto cio' che supera questo
    # controllo — e su un backend che supporta il controllo (Linux) e' l'unica
    # cosa che decide.
    flags = getattr(b, "capability_flags", None)
    try:
        supported = dict(flags()).get("service_control") if callable(flags) else None
    except Exception:  # noqa: BLE001
        supported = None
    if supported is False:
        out = unsupported(b, "service_control", "result")
        out.pop("result", None)
        out.update({
            "ok": False,
            "name": name,
            "action": action,
            # `error` oltre a `reason`: la route legge `error` per comporre il
            # 501, e senza finirebbe per rispondere con un messaggio generico —
            # cioe' perderebbe proprio la spiegazione che questa PR aggiunge.
            "error": out["reason"],
        })
        return out

    try:
        canonical = check_service(name, action)
    except ServiceRejected as exc:
        return rejection(exc, name, action)

    # LinuxBackend accetta scope; Fake/Windows no
    try:
        return b.control_service(canonical, action, scope=scope)  # type: ignore[call-arg]
    except TypeError:
        return b.control_service(canonical, action)
