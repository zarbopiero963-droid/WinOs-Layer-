"""Registry read/write with policy."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend
from windows_os_api.os.registry.allowlist import (
    RegistryPathRejected,
    check as check_path,
    rejection,
)


def read(path: str, name: str | None = None) -> dict[str, Any]:
    """Lettura: NON passa dall'allowlist di scrittura.

    L'allowlist di prefissi risponde alla domanda «dove possiamo SCRIVERE»
    (decisione owner D2-B), e leggere e' una classe di rischio diversa. Non
    l'ho estesa alla lettura di mia iniziativa: sarebbe stato allargare lo scope
    oltre la decisione presa. Segnalato all'owner come osservazione aperta.
    """
    return get_backend().registry_read(path, name)


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
