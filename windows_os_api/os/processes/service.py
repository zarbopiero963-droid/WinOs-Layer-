"""Process management.

N047 — il gate sta QUI, non nei chiamanti. `POST /v1/processes` è oggi l'unico
ingresso, ma un secondo ingresso che chiamasse il backend direttamente
salterebbe la policy: mettere la decisione nel service significa che ogni
ingresso la attraversa per costruzione, non per memoria di chi lo scrive.

N048 — albero, restart, teardown. Inventario parent/children/modules/threads/
handles/resources; terminate e restart limitati ai processi posseduti; dopo un
teardown i discendenti del root devono essere zero, altrimenti non è un
successo (è un falso successo con orfani).

Domande distinte, moduli distinti:

* `exec_policy` — questo avvio è permesso?
* `identity` — questo PID è ancora il processo che credo?
* `tree` — cos'altro vive sotto di lui, e dopo il terminate è rimasto qualcosa?
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
from windows_os_api.os.processes import tree as proc_tree


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
    # N048: argv va registrato qui — senza di esso un restart inventerebbe la
    # riga di comando, e inventare non è ripristinare.
    observed = get_backend().get_process(pid)
    identity = ident.identity_from_process(observed, pid=pid)
    identity = ident.ProcessIdentity(
        pid=pid,
        create_time=identity.create_time,
        exe=identity.exe or exe,
        owner=identity.owner or ident.current_user(),
        argv=tuple(cleaned_args),
    )
    ident.get_registry().record(identity)

    enriched = dict(result)
    enriched["exe"] = exe
    enriched["create_time"] = identity.create_time
    enriched["owner"] = identity.owner
    enriched["argv"] = list(identity.argv)
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

    N048: dopo il gate, l'albero sotto il root viene abbattuto (figli prima del
    padre). Se restano discendenti vivi la risposta è ``ok=False`` con
    ``PROCESS_RESIDUAL_CHILDREN`` — non un successo con orfani.
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

    # N048: i discendenti si raccolgono PRIMA di uccidere il root, altrimenti
    # dopo non sapremmo più chi erano. Si terminano dal più profondo al root:
    # un figlio vivo dopo un "ok" sul padre era il falso successo della Phase 0.
    descendants = proc_tree.descendant_pids(backend, pid)
    killed_children: list[int] = []
    child_errors: list[dict[str, Any]] = []
    for child_pid in reversed(descendants):
        child_result = backend.terminate_process(child_pid)
        if isinstance(child_result, dict) and child_result.get("ok"):
            killed_children.append(child_pid)
            registry.forget(child_pid)
        else:
            child_errors.append(
                {
                    "pid": child_pid,
                    "error": (child_result or {}).get("error")
                    if isinstance(child_result, dict)
                    else "terminate_failed",
                    "code": (child_result or {}).get("code")
                    if isinstance(child_result, dict)
                    else None,
                }
            )

    result = backend.terminate_process(pid)
    if not isinstance(result, dict):
        result = {"ok": False, "error": "risposta backend non valida", "pid": pid}
    else:
        result = dict(result)

    residual = proc_tree.residual_alive(backend, descendants)
    result["terminated_children"] = killed_children
    result["child_errors"] = child_errors
    result["residual_children"] = residual
    result["tree_teardown"] = True

    if residual:
        # Non è un successo: il root (o parte dell'albero) è stato toccato, ma
        # restano orfani. Dichiarare ok=True qui riprodurrebbe il falso successo.
        result["ok"] = False
        result["code"] = proc_tree.PROCESS_RESIDUAL_CHILDREN
        result["error"] = (
            f"teardown incompleto per il pid {pid}: restano figli vivi {residual}"
        )
        # Il root può essere morto comunque: togliamo il record se non c'è più,
        # senza fingere che l'albero sia pulito.
        if backend.get_process(pid) is None:
            registry.forget(pid)
        return result

    if result.get("ok"):
        registry.forget(pid)
    return result


def inspect_process(pid: int) -> dict[str, Any]:
    """Inventario parent/children/threads/modules/handles/resources (N048)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": f"pid non valido: {pid!r}",
            "code": proc_tree.PROCESS_TREE_NOT_FOUND,
        }
    backend = get_backend()
    info = proc_tree.inventory(backend, pid)
    if info is None:
        return {
            "ok": False,
            "error": f"processo {pid} non trovato",
            "code": proc_tree.PROCESS_TREE_NOT_FOUND,
            "pid": pid,
        }
    out = dict(info)
    out["ok"] = True
    out["owned_by_runtime"] = ident.get_registry().get(pid) is not None
    return out


def get_process_tree(pid: int) -> dict[str, Any]:
    """Albero nested a partire da `pid` (N048)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": f"pid non valido: {pid!r}",
            "code": proc_tree.PROCESS_TREE_NOT_FOUND,
        }
    backend = get_backend()
    node = proc_tree.build_tree(backend, pid)
    if node is None:
        return {
            "ok": False,
            "error": f"processo {pid} non trovato",
            "code": proc_tree.PROCESS_TREE_NOT_FOUND,
            "pid": pid,
        }
    return {
        "ok": True,
        "pid": pid,
        "tree": node,
        "descendant_count": len(proc_tree.descendant_pids(backend, pid)),
        "owned_by_runtime": ident.get_registry().get(pid) is not None,
    }


def restart_process(
    pid: int,
    *,
    expect_create_time: float | None = None,
    expect_name: str | None = None,
) -> dict[str, Any]:
    """Riavvia un processo posseduto: teardown dell'albero + stesso exe/argv.

    Solo i processi che *questo* runtime ha avviato (record nel registro con
    exe+argv) si possono restartare. Senza argv registrato il restart si
    rifiuta: indovinare la riga di comando non è ripristinare.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": f"pid non valido: {pid!r}",
            "code": ident.PROCESS_NOT_FOUND,
        }

    registry = ident.get_registry()
    recorded = registry.get(pid)
    if recorded is None:
        return {
            "ok": False,
            "error": (
                f"il processo {pid} non è stato avviato da questa API: "
                f"il restart è consentito solo sui processi posseduti"
            ),
            "code": proc_tree.PROCESS_RESTART_NOT_OWNED,
            "pid": pid,
        }
    if not recorded.exe:
        return {
            "ok": False,
            "error": (
                f"il processo {pid} non ha un eseguibile registrato: "
                f"restart rifiutato invece di inventare un comando"
            ),
            "code": proc_tree.PROCESS_RESTART_UNKNOWN,
            "pid": pid,
        }

    exe = recorded.exe
    argv = list(recorded.argv)

    # Stesso gate di terminate: se il PID è stato riciclato non si riparte
    # «sopra» un altro processo, e non si uccide l'albero sbagliato.
    stopped = terminate_process(
        pid,
        expect_create_time=expect_create_time,
        expect_name=expect_name,
    )
    if not stopped.get("ok", True) and stopped.get("code") in {
        ident.PROCESS_IDENTITY_MISMATCH,
        ident.PROCESS_IDENTITY_REQUIRED,
        ident.PROCESS_PROTECTED,
        proc_tree.PROCESS_RESIDUAL_CHILDREN,
    }:
        return {
            "ok": False,
            "error": (
                f"restart di {pid} bloccato dal teardown: {stopped.get('error')}"
            ),
            "code": stopped.get("code"),
            "pid": pid,
            "teardown": stopped,
        }

    launched = start_process(exe, argv)
    if not launched.get("ok", True):
        return {
            "ok": False,
            "error": (
                f"teardown di {pid} riuscito ma il relaunch è fallito: "
                f"{launched.get('error')}"
            ),
            "code": launched.get("code") or proc_tree.PROCESS_RESTART_UNKNOWN,
            "pid": pid,
            "teardown": stopped,
            "launch": launched,
        }

    return {
        "ok": True,
        "action": "restarted",
        "old_pid": pid,
        "pid": launched.get("pid"),
        "exe": exe,
        "argv": argv,
        "create_time": launched.get("create_time"),
        "owner": launched.get("owner"),
        "teardown": {
            "terminated_children": stopped.get("terminated_children", []),
            "residual_children": stopped.get("residual_children", []),
        },
        "launch": launched,
    }
