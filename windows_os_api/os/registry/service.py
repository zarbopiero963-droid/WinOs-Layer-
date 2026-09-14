"""Registry read/write with policy."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend
from windows_os_api.os.registry.allowlist import (
    RegistryPathRejected,
    check as check_path,
    check_read,
    filter_values,
    rejection,
)


def read(path: str, name: str | None = None) -> dict[str, Any]:
    """Lettura fail-closed — allowlist + denylist + filtro valori (D6 / N005).

    `check_read` gira **prima** di qualunque backend/`OpenKey`: percorso fuori
    allowlist o in denylist non raggiunge il registro. `registry.read` resta
    anche a VIEWER: la permission non supera la policy di percorso (#64 D6).

    Il controllo sta qui e non nella route (#13).
    """
    try:
        canonical = check_read(path, name)
    except RegistryPathRejected as exc:
        return rejection(exc, path, name)

    result = get_backend().registry_read(canonical, name)

    # Chiedere la chiave senza `name` restituisce TUTTI i suoi valori: senza
    # questo passaggio il controllo sul nome si aggirerebbe in una mossa sola.
    if isinstance(result, dict) and isinstance(result.get("values"), dict):
        kept, withheld = filter_values(result["values"])
        if withheld:
            result = {**result, "values": kept, "withheld": sorted(withheld)}
    return result


def write(path: str, name: str, value: Any) -> dict[str, Any]:
    """Scrittura — solo sotto i prefissi autorizzati (decisione owner D2-B).

    Il controllo sta PRIMA di qualunque cosa raggiunga il backend: un percorso
    non autorizzato non arriva mai al registro, non «fallisce dopo averci
    provato». E sta qui, non nella route, perche' questo e' il punto che ogni
    superficie attraversa — un controllo che il chiamante puo' dimenticare non
    e' un controllo (#13).

    Il ruolo del chiamante non entra: ADMIN non e' una scorciatoia attorno
    all'allowlist, per decisione esplicita dell'owner.
    """
    try:
        canonical = check_path(path)
    except RegistryPathRejected as exc:
        return rejection(exc, path, name)
    return get_backend().registry_write(canonical, name, value)
