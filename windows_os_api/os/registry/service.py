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
    """Lettura — fuori dalle aree vietate (decisione owner D6).

    Fino a questa patch non c'era **nessun** controllo: si leggeva qualunque
    hive, e `registry.read` e' una permission che ha anche `VIEWER`, il ruolo
    piu' basso. Bastava chiedere il percorso giusto per farsi restituire il
    `DefaultPassword` dell'autologon o l'`HKCU` di un altro utente.

    L'owner ha scelto la **denylist**, non l'allowlist simmetrica alla
    scrittura: le letture esistenti continuano a funzionare, e cio' che nessuno
    ha elencato resta leggibile. Il compromesso e' scritto per esteso in
    `allowlist.py`.

    Il controllo sta qui e non nella route per la stessa ragione della
    scrittura: questo e' il punto che ogni superficie attraversa, e un controllo
    che il chiamante puo' dimenticare non e' un controllo (#13).
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
