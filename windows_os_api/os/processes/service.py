"""Process management.

N047 — il gate sta QUI, non nei chiamanti. `POST /v1/processes` è oggi l'unico
ingresso, ma un secondo ingresso che chiamasse il backend direttamente
salterebbe la policy: mettere la decisione nel service significa che ogni
ingresso la attraversa per costruzione, non per memoria di chi lo scrive.

Due domande distinte, due moduli:

* `exec_policy` — questo avvio è permesso? (interprete con codice inline, area
  scrivibile da chiunque, percorso ambiguo => no)
* `identity` — questo PID è ancora il processo che credo? (create_time + exe +
  owner => il PID riciclato non viene terminato al posto suo)
"""
from __future__ import annotations

from typing import Any

from windows_os_api.backends.factory import get_backend
from windows_os_api.os.capability import discover
from windows_os_api.os.processes import identity as ident
from windows_os_api.os.processes.exec_policy import (
    ProcessLaunchRejected,
    authorize,
)


def list_processes() -> dict[str, Any]:
    """Processi, col contratto `supported` (D3 / N004).

    Usa il flag backend `processes` (False senza psutil → UNAVAILABLE / NOT_SUPPORTED
    secondo NOT_IMPLEMENTED). Chiave `processes` invariata (additivo).
    """
    b = get_backend()
    return discover(b, "processes", "processes", b.list_processes)


def get_process(pid: int) -> dict[str, Any] | None:
    return get_backend().get_process(pid)


def start_process(command: str, args: list[str] | None = None) -> dict[str, Any]:
    """Avvia un'applicazione, se la policy lo consente.

    Al backend arriva il percorso **risolto** da `authorize`, non la stringa del
    chiamante: decidere su un file e poi lasciare che il `PATH` ne scelga un
    altro allo spawn sarebbe una finestra TOCTOU aperta di proposito.
    """
    try:
        exe, cleaned_args = authorize(command, args)
    except ProcessLaunchRejected as rejected:
        return {
            "ok": False,
            "error": str(rejected),
            "code": rejected.code,
            "command": command if isinstance(command, str) else repr(command),
        }

    result = get_backend().start_process(exe, cleaned_args)
    if not isinstance(result, dict) or not result.get("ok", True):
        return result

    pid = result.get("pid")
    if not isinstance(pid, int):
        return result

    # L'identità si legge dal sistema, non si assume: se il processo è già
    # uscito, `create_time` manca e resta l'eseguibile autorizzato come prova.
    observed = get_backend().get_process(pid)
    identity = ident.identity_from_process(observed, pid=pid)
    if identity.exe is None:
        identity = ident.ProcessIdentity(
            pid=pid,
            create_time=identity.create_time,
            exe=exe,
            owner=identity.owner or ident.current_user(),
        )
    ident.get_registry().record(identity)

    enriched = dict(result)
    enriched["exe"] = exe
    enriched["create_time"] = identity.create_time
    enriched["owner"] = identity.owner
    enriched["identity_recorded"] = True
    return enriched


def terminate_process(
    pid: int,
    *,
    expect_create_time: float | None = None,
    expect_name: str | None = None,
) -> dict[str, Any]:
    """Termina un processo **verificandone l'identità** prima dell'effetto.

    Tre casi, e nessuno di essi è «fidati del numero»:

    1. processo avviato da questo runtime e ancora sé stesso → terminato;
    2. processo avviato da questo runtime ma con identità diversa (PID
       riciclato) → rifiutato, e il record obsoleto viene dimenticato;
    3. processo che questo runtime non ha avviato → serve un'attesa esplicita
       (`expect_create_time` o `expect_name`) che combaci, e deve appartenere
       allo stesso utente.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": f"pid non valido: {pid!r}",
            "code": ident.PROCESS_NOT_FOUND,
        }
    if pid <= 1:
        # pid 1 è init/systemd; 0 e i negativi sono gruppi di processi, non
        # processi: `kill(-1)` colpirebbe tutto ciò che l'utente possiede.
        return {
            "ok": False,
            "error": f"il pid {pid} non è terminabile da questa API",
            "code": ident.PROCESS_PROTECTED,
            "pid": pid,
        }

    backend = get_backend()
    observed = backend.get_process(pid)
    current = ident.identity_from_process(observed, pid=pid)
    registry = ident.get_registry()
    recorded = registry.get(pid)

    if recorded is not None:
        if observed is None:
            # Il processo che avevamo avviato non c'è più: niente da terminare,
            # e il record non deve sopravvivergli e coprire un PID riciclato.
            registry.forget(pid)
            return {
                "ok": False,
                "error": f"il processo {pid} non esiste più",
                "code": ident.PROCESS_NOT_FOUND,
                "pid": pid,
            }
        if not ident.same_process(recorded, current):
            registry.forget(pid)
            return {
                "ok": False,
                "error": (
                    f"il pid {pid} non è più il processo avviato da questa API "
                    f"(create_time atteso {recorded.create_time!r}, "
                    f"trovato {current.create_time!r}): PID riciclato"
                ),
                "code": ident.PROCESS_IDENTITY_MISMATCH,
                "pid": pid,
            }
    else:
        if observed is None:
            return {
                "ok": False,
                "error": f"processo {pid} non trovato",
                "code": ident.PROCESS_NOT_FOUND,
                "pid": pid,
            }
        if expect_create_time is None and not expect_name:
            return {
                "ok": False,
                "error": (
                    f"il processo {pid} non è stato avviato da questa API: per "
                    f"terminarlo serve un'attesa esplicita (expect_create_time "
                    f"o expect_name) che ne confermi l'identità"
                ),
                "code": ident.PROCESS_IDENTITY_REQUIRED,
                "pid": pid,
            }
        expected = ident.ProcessIdentity(
            pid=pid,
            create_time=expect_create_time,
            exe=expect_name or current.exe,
        )
        matches_time = (
            expect_create_time is not None
            and current.create_time is not None
            and abs(expect_create_time - current.create_time)
            <= ident.CREATE_TIME_TOLERANCE
        )
        matches_name = bool(
            expect_name
            and str(current.exe or observed.get("name") or "").lower().endswith(
                expect_name.lower()
            )
        )
        if not (matches_time or matches_name):
            return {
                "ok": False,
                "error": (
                    f"l'identità attesa non corrisponde al processo {pid} "
                    f"(atteso {expected.as_dict()}, trovato {current.as_dict()})"
                ),
                "code": ident.PROCESS_IDENTITY_MISMATCH,
                "pid": pid,
            }
        owner = current.owner
        me = ident.current_user()
        if owner and me and owner != me:
            return {
                "ok": False,
                "error": (
                    f"il processo {pid} appartiene a {owner!r}, non a {me!r}: "
                    f"questa API non termina processi di altri utenti"
                ),
                "code": ident.PROCESS_PROTECTED,
                "pid": pid,
            }

    result = backend.terminate_process(pid)
    if isinstance(result, dict) and result.get("ok"):
        registry.forget(pid)
    return result
