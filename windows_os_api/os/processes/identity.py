"""N047 — L'identità di un processo non è il suo PID.

Il PID viene riciclato. Su Linux il contatore gira e riparte, su Windows gli
handle vengono riassegnati: il numero che identificava il processo avviato un
minuto fa può appartenere a un altro processo adesso. `terminate_process(pid)`
che si fida del solo numero termina quello che c'è, non quello che intendeva.

Riscontro su main prima di N047::

    victim = subprocess.Popen(["sleep", "60"])    # mai avviato da questa API
    backend.terminate_process(victim.pid)
    -> {"ok": True, "pid": 741, "status": "terminated", "real": True}

Nessuna verifica: né di chi ha avviato il processo, né di quando è nato, né di
quale eseguibile sia. Qui l'identità è la tripla `(pid, create_time, exe)` più
il proprietario, e `create_time` è ciò che rende il PID riciclato distinguibile:
è assegnato dal kernel alla nascita e non si ripete per lo stesso PID.

Il registro vive quanto il runtime. Un processo avviato da un ciclo precedente
non è "nostro" nel ciclo successivo: la sua identità non è stata osservata da
questo runtime, quindi torna nel caso generale (serve l'attesa esplicita).
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any

PROCESS_IDENTITY_MISMATCH = "PROCESS_IDENTITY_MISMATCH"
PROCESS_IDENTITY_REQUIRED = "PROCESS_IDENTITY_REQUIRED"
PROCESS_NOT_FOUND = "PROCESS_NOT_FOUND"
PROCESS_PROTECTED = "PROCESS_PROTECTED"

# create_time in secondi: due processi con lo stesso PID non nascono nello
# stesso centesimo di secondo, ma il valore riletto puo' differire nell'ultima
# cifra a seconda della fonte. La tolleranza e' stretta di proposito.
CREATE_TIME_TOLERANCE = 0.05


@dataclass(frozen=True)
class ProcessIdentity:
    """Ciò che rende un processo *quel* processo.

    ``argv`` (N048) non entra nel confronto di identità: serve al restart, che
    deve rilanciare *la stessa* riga di comando che avevamo autorizzato, non
    inventarne una. Senza argv registrato il restart si rifiuta invece di
    indovinare.
    """

    pid: int
    create_time: float | None = None
    exe: str | None = None
    owner: str | None = None
    argv: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "create_time": self.create_time,
            "exe": self.exe,
            "owner": self.owner,
            "argv": list(self.argv),
        }


def identity_from_process(info: Any, *, pid: int | None = None) -> ProcessIdentity:
    """Estrae l'identità da ciò che il backend riporta su un processo."""
    if not isinstance(info, dict):
        return ProcessIdentity(pid=int(pid or 0))
    raw_pid = info.get("pid", pid)
    try:
        resolved_pid = int(raw_pid)
    except (TypeError, ValueError):
        resolved_pid = int(pid or 0)
    create_time = info.get("create_time")
    try:
        create_time = float(create_time) if create_time is not None else None
    except (TypeError, ValueError):
        create_time = None
    exe = info.get("exe") or info.get("command")
    owner = info.get("owner") or info.get("username")
    return ProcessIdentity(
        pid=resolved_pid,
        create_time=create_time,
        exe=str(exe) if exe else None,
        owner=str(owner) if owner else None,
    )


def same_process(recorded: ProcessIdentity, current: ProcessIdentity) -> bool:
    """Il processo vivo è quello registrato?

    Fail-closed sul dato che conta: se `create_time` è noto da entrambe le parti
    deve coincidere. Se una delle due parti non lo espone (backend senza psutil,
    o processo già sparito) l'identità **non** è confermata da un PID uguale, e
    resta la sola prova disponibile: l'eseguibile.
    """
    if recorded.pid != current.pid:
        return False
    if recorded.create_time is not None and current.create_time is not None:
        return abs(recorded.create_time - current.create_time) <= CREATE_TIME_TOLERANCE
    if recorded.exe and current.exe:
        return os.path.normcase(recorded.exe) == os.path.normcase(current.exe)
    return False


class ProcessRegistry:
    """I processi avviati da questo runtime, con la loro identità alla nascita."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_pid: dict[int, ProcessIdentity] = {}

    def record(self, identity: ProcessIdentity) -> None:
        with self._lock:
            self._by_pid[identity.pid] = identity

    def get(self, pid: int) -> ProcessIdentity | None:
        with self._lock:
            return self._by_pid.get(pid)

    def forget(self, pid: int) -> None:
        with self._lock:
            self._by_pid.pop(pid, None)

    def clear(self) -> None:
        with self._lock:
            self._by_pid.clear()

    def known_pids(self) -> list[int]:
        with self._lock:
            return sorted(self._by_pid)


_registry = ProcessRegistry()


def get_registry() -> ProcessRegistry:
    return _registry


def reset_registry() -> None:
    """Sgancia i processi registrati (isolamento test / nuovo ciclo runtime)."""
    _registry.clear()


def current_user() -> str | None:
    """Chi siamo, per decidere se un processo è nostro da terminare."""
    try:
        import getpass

        return getpass.getuser()
    except Exception:  # noqa: BLE001 - un ambiente senza utente non fa fallire il gate
        return None


__all__ = [
    "CREATE_TIME_TOLERANCE",
    "PROCESS_IDENTITY_MISMATCH",
    "PROCESS_IDENTITY_REQUIRED",
    "PROCESS_NOT_FOUND",
    "PROCESS_PROTECTED",
    "ProcessIdentity",
    "ProcessRegistry",
    "current_user",
    "get_registry",
    "identity_from_process",
    "reset_registry",
    "same_process",
]
