"""N046 — una sola istanza runtime per adapter store.

Due istanze puntate sullo stesso store scrivono gli stessi manifest senza alcun
lock fra processi: il lock di N045 e' per processo, quindi la seconda istanza
sovrascrive in silenzio il lavoro della prima. Nessuna delle due se ne accorge,
entrambe rispondono `200`, ed e' esattamente il falso successo che questo modulo
chiude: l'avvio prende un lock esclusivo sulla cartella dello store, e la seconda
istanza viene rifiutata invece di partire.

**La liveness si decide sul PID, mai sull'orologio.** Un TTL a tempo sarebbe
rilasciato da un salto di clock (caso H63-N046) mentre il processo e' vivo, e
tenuto per ore dopo un crash. Il PID risponde alla domanda giusta — quel processo
esiste ancora? — e non dipende da che ora crede che sia la macchina.

Un lock lasciato da un processo morto viene recuperato, come uno illeggibile:
rifiutare l'avvio per un file corrotto renderebbe un crash irreversibile senza
intervento manuale. Un lock di un processo **vivo** non viene mai rubato.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

LOCK_FILENAME = ".winos-instance.lock"

# Quanti giri di «recupera un lock morto e riprova a crearlo» prima di
# rinunciare. Senza un tetto, due processi che si recuperano il lock a vicenda
# girerebbero all'infinito invece di fallire in modo visibile.
_RECLAIM_ATTEMPTS = 5


class InstanceLockTaken(RuntimeError):
    """Un'altra istanza viva possiede gia' questo store."""


def _pid_alive(pid: int) -> bool:
    """Il processo esiste? Conservativo: nel dubbio lo considera vivo.

    Dire «morto» per errore fa rubare il lock a un'istanza attiva, cioe' riapre
    il difetto. Dire «vivo» per errore costa al massimo un avvio rifiutato, che
    e' rumoroso e correggibile.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - percorso Windows, coperto in CI
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Esiste, ma non e' nostro: vivo.
        return True
    except OSError:
        return True
    return True


def read_holder(store_dir: Path) -> dict[str, Any] | None:
    """Chi dice di avere il lock, o ``None`` se assente/illeggibile/malformato."""
    path = Path(store_dir) / LOCK_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if not isinstance(pid, int):
        return None
    return data


class InstanceLock:
    """Lock di istanza sullo store. Preso all'avvio, rilasciato al teardown."""

    def __init__(self, store_dir: Path | str) -> None:
        self.store_dir = Path(store_dir)
        self.path = self.store_dir / LOCK_FILENAME
        self.held = False
        self._payload: dict[str, Any] | None = None

    def acquire(self) -> dict[str, Any]:
        """Prende il lock, o solleva `InstanceLockTaken`.

        Il vincitore lo decide il kernel, non noi: la creazione con
        ``O_CREAT | O_EXCL`` riesce a **un solo** chiamante, qualunque sia
        l'interleaving. Leggere e poi scrivere, com'era prima, lascia fra il
        controllo e l'effetto una finestra in cui due processi leggono entrambi
        «libero» e scrivono entrambi: con 12 processi concorrenti il lock
        risultava acquisito da 3, e 9 morivano con `FileNotFoundError` perche'
        condividevano lo stesso file temporaneo.
        """
        self.store_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "host": _hostname(),
            "started_at": time.time(),
        }
        # `started_at` e' diagnostico: serve a un umano che legge il file, non al
        # gate. Nessuna decisione di liveness lo guarda (vedi docstring del modulo).
        blob = json.dumps(payload, indent=2).encode("utf-8")

        for _attempt in range(_RECLAIM_ATTEMPTS):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                holder = read_holder(self.store_dir)
                if holder is not None and _pid_alive(int(holder["pid"])):
                    raise InstanceLockTaken(
                        f"un'altra istanza WinOs (pid {holder['pid']}) usa gia' lo "
                        f"store {self.store_dir}: due istanze sullo stesso store si "
                        f"sovrascrivono i manifest a vicenda. Fermare l'altra "
                        f"istanza o usare WINOS_ADAPTER_STORE diverso."
                    ) from None
                # Lock di un processo morto, o file illeggibile: si recupera. Il
                # ciclo riprova la creazione esclusiva, cosi' anche fra due
                # recuperanti ne resta comunque uno solo.
                self._discard_stale()
                continue
            except OSError as exc:
                raise InstanceLockTaken(
                    f"impossibile prendere il lock di istanza su {self.store_dir}: {exc}"
                ) from exc
            with os.fdopen(fd, "wb") as handle:
                handle.write(blob)
            self.held = True
            self._payload = payload
            return payload

        raise InstanceLockTaken(
            f"contesa irrisolta sul lock di istanza in {self.store_dir} dopo "
            f"{_RECLAIM_ATTEMPTS} tentativi: nessun avvio, invece di due istanze"
        )

    def _discard_stale(self) -> None:
        """Toglie un lock che nessun processo vivo possiede.

        Rilegge e ricontrolla la liveness immediatamente prima di cancellare: fra
        il controllo del chiamante e questo punto un'altra istanza puo' avere
        preso il lock, e quella non va toccata. Se sparisce da sola nel
        frattempo, va bene lo stesso — il ciclo riprova la creazione esclusiva.
        """
        holder = read_holder(self.store_dir)
        if holder is not None and _pid_alive(int(holder["pid"])):
            return
        try:
            self.path.unlink()
        except (FileNotFoundError, OSError):
            return

    def release(self) -> bool:
        """Toglie il lock solo se e' ancora nostro. ``False`` se non c'era."""
        if not self.held:
            return False
        self.held = False
        holder = read_holder(self.store_dir)
        if holder is None or holder.get("pid") != os.getpid():
            # Qualcun altro l'ha recuperato: non e' piu' roba nostra da cancellare.
            return False
        try:
            self.path.unlink()
            return True
        except (FileNotFoundError, OSError):
            return False


def _hostname() -> str:
    try:
        import socket

        return socket.gethostname()
    except Exception:  # noqa: BLE001 - un nome host mancante non blocca l'avvio
        return "unknown"
