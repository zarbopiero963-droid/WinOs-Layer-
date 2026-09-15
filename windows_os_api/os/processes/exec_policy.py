"""N047 — Chi può essere avviato, e con quali argomenti.

Perché è separata dal registro del terminale
--------------------------------------------
`os/terminal/allowlist.py` registra comandi diagnostici con argomenti fissi:
risponde alla domanda «questo servizio può eseguire *questo comando*?». Qui la
domanda è un'altra — «questo servizio può *lanciare un'applicazione* che il
chiamante ha scelto?» — e la risposta non può essere la stessa lista, perché
allargare l'una allargherebbe l'altra. Due confini, due file.

Cosa c'era prima
----------------
`POST /v1/processes` passava `command` e `args` direttamente a `Popen`. Nessuna
policy in mezzo::

    start_process("/bin/sh", ["-c", "sleep 30"])  ->  {"ok": True, "pid": 740}

Un interprete con codice inline è esecuzione arbitraria: qualunque allowlist di
eseguibili la si aggira chiedendo `sh -c`. Ed è l'unica risposta che il
chiamante riceveva — `ok: True` — identica a quella di un lancio legittimo.

Cosa NON fa questa policy
-------------------------
Non è un'allowlist di programmi. Questo prodotto esiste per pilotare le
applicazioni installate sulla macchina: elencarle una per una renderebbe il
prodotto inutile senza rendere il sistema più sicuro. Chiude invece le forme di
lancio che sono arbitrarie *per costruzione*:

1. **interpreti con codice inline** (`sh -c`, `python -c`, `cmd /c`, …);
2. **eseguibili in aree scrivibili da chiunque** (`/tmp`, `%TEMP%`, …), dove
   depositare un binario e chiederne l'avvio è la scalata classica;
3. **percorsi ambigui**: relativi, con `..`, o risolti dal `PATH` al momento
   dello spawn invece che al momento della decisione (TOCTOU).

Il chiamante riceve il percorso realmente autorizzato, ed è quello che viene
eseguito: la decisione e l'effetto guardano lo stesso file.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

EXECUTABLE_NOT_FOUND = "EXECUTABLE_NOT_FOUND"
EXECUTABLE_PATH_INVALID = "EXECUTABLE_PATH_INVALID"
EXECUTABLE_LOCATION_FORBIDDEN = "EXECUTABLE_LOCATION_FORBIDDEN"
INTERPRETER_INLINE_CODE_FORBIDDEN = "INTERPRETER_INLINE_CODE_FORBIDDEN"
ARGS_INVALID = "ARGS_INVALID"

MAX_ARGS = 64
MAX_ARG_LEN = 4096

# Interpreti che, con questi flag, eseguono codice passato sulla riga di comando.
# La chiave è il nome del file senza estensione, minuscolo.
_INLINE_CODE_FLAGS: dict[str, frozenset[str]] = {
    "sh": frozenset({"-c"}),
    "bash": frozenset({"-c"}),
    "dash": frozenset({"-c"}),
    "zsh": frozenset({"-c"}),
    "ksh": frozenset({"-c"}),
    "csh": frozenset({"-c"}),
    "tcsh": frozenset({"-c"}),
    "fish": frozenset({"-c"}),
    "python": frozenset({"-c"}),
    "python3": frozenset({"-c"}),
    "perl": frozenset({"-e", "-E"}),
    "ruby": frozenset({"-e"}),
    "node": frozenset({"-e", "-p", "--eval", "--print"}),
    "php": frozenset({"-r"}),
    "cmd": frozenset({"/c", "/k"}),
    "powershell": frozenset({"-command", "-c", "-encodedcommand", "-e"}),
    "pwsh": frozenset({"-command", "-c", "-encodedcommand", "-e"}),
    "wscript": frozenset({"/e"}),
    "cscript": frozenset({"/e"}),
}


class ProcessLaunchRejected(Exception):
    """L'avvio non è permesso. Porta il codice e il motivo mostrato al chiamante."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _volatile_roots() -> tuple[Path, ...]:
    """Aree in cui chiunque scrive: un eseguibile lì non è un'applicazione installata.

    Non si riaprono per configurazione. Un flag che rendesse lanciabile `/tmp`
    riporterebbe esattamente al buco che questa funzione chiude.
    """
    roots: list[Path] = []
    for candidate in (
        tempfile.gettempdir(),
        "/tmp",
        "/var/tmp",
        "/dev/shm",
        os.environ.get("TEMP"),
        os.environ.get("TMP"),
    ):
        if not candidate:
            continue
        try:
            roots.append(Path(candidate).resolve())
        except OSError:
            continue
    home = Path.home()
    for name in ("Downloads", "Scaricati"):
        roots.append(home / name)
    return tuple(roots)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_args(args: list[str] | None) -> list[str]:
    if args is None:
        return []
    if not isinstance(args, (list, tuple)):
        raise ProcessLaunchRejected(
            "gli argomenti devono essere una lista di stringhe", code=ARGS_INVALID
        )
    if len(args) > MAX_ARGS:
        raise ProcessLaunchRejected(
            f"troppi argomenti: {len(args)} (massimo {MAX_ARGS})", code=ARGS_INVALID
        )
    cleaned: list[str] = []
    for arg in args:
        if not isinstance(arg, str):
            raise ProcessLaunchRejected(
                f"argomento non testuale: {arg!r}", code=ARGS_INVALID
            )
        if "\x00" in arg:
            # Un NUL tronca la stringa a livello di syscall: l'argomento che il
            # gate legge e quello che il kernel riceve sarebbero diversi.
            raise ProcessLaunchRejected(
                "un argomento contiene un byte NUL", code=ARGS_INVALID
            )
        if len(arg) > MAX_ARG_LEN:
            raise ProcessLaunchRejected(
                f"argomento troppo lungo ({len(arg)} caratteri)", code=ARGS_INVALID
            )
        cleaned.append(arg)
    return cleaned


def _resolve_executable(command: object) -> Path:
    if not isinstance(command, str) or not command.strip():
        raise ProcessLaunchRejected(
            "il comando deve essere una stringa non vuota", code=EXECUTABLE_PATH_INVALID
        )
    raw = command.strip()
    if "\x00" in raw:
        raise ProcessLaunchRejected(
            "il comando contiene un byte NUL", code=EXECUTABLE_PATH_INVALID
        )

    # `os.altsep` e' None su POSIX: scriverlo come `(os.altsep or "") in raw`
    # renderebbe la condizione sempre vera, perche' la stringa vuota e' contenuta
    # in qualunque stringa, e ogni nome nudo verrebbe trattato come percorso.
    looks_like_path = os.sep in raw or (os.altsep is not None and os.altsep in raw)
    if looks_like_path:
        candidate = Path(raw)
        if ".." in candidate.parts:
            # Risolvere `..` significherebbe indovinare cosa intendeva il
            # chiamante; un gate non indovina.
            raise ProcessLaunchRejected(
                f"{raw!r} contiene segmenti relativi", code=EXECUTABLE_PATH_INVALID
            )
        if not candidate.is_absolute():
            raise ProcessLaunchRejected(
                f"{raw!r} non è un percorso assoluto", code=EXECUTABLE_PATH_INVALID
            )
        resolved = candidate
    else:
        # Nome nudo: si risolve QUI, non allo spawn. Risolverlo due volte
        # significherebbe decidere su un file e poi eseguirne un altro.
        found = shutil.which(raw)
        if not found:
            raise ProcessLaunchRejected(
                f"eseguibile non trovato: {raw!r}", code=EXECUTABLE_NOT_FOUND
            )
        resolved = Path(found)

    try:
        real = resolved.resolve()
    except OSError as exc:
        raise ProcessLaunchRejected(
            f"percorso non risolvibile: {raw!r} ({exc})", code=EXECUTABLE_PATH_INVALID
        ) from exc
    if not real.is_file():
        raise ProcessLaunchRejected(
            f"eseguibile non trovato: {raw!r}", code=EXECUTABLE_NOT_FOUND
        )
    return real


def _refuse_volatile_location(exe: Path) -> None:
    for root in _volatile_roots():
        if _is_under(exe, root):
            raise ProcessLaunchRejected(
                f"{exe} si trova in un'area scrivibile da chiunque ({root}): "
                f"un binario depositato lì non è un'applicazione installata",
                code=EXECUTABLE_LOCATION_FORBIDDEN,
            )


def _interpreter_stem(name: str) -> str:
    """`python3.11` e `python3` sono `python`: il divieto non dipende dalla minor.

    Senza questa normalizzazione basterebbe chiedere l'interprete col numero di
    versione nel nome — che e' come si chiama davvero su quasi ogni distribuzione
    — per non essere riconosciuto.
    """
    stem = name.lower()
    if stem.endswith(".exe"):
        stem = stem[: -len(".exe")]
    trimmed = stem.rstrip("0123456789.")
    return trimmed or stem


def _refuse_inline_code(exe: Path, args: list[str]) -> None:
    stem = _interpreter_stem(exe.name)
    flags = _INLINE_CODE_FLAGS.get(stem)
    if not flags:
        return
    for arg in args:
        if arg.lower() in flags:
            raise ProcessLaunchRejected(
                f"{exe.name} con {arg!r} esegue codice passato sulla riga di comando: "
                f"è esecuzione arbitraria, non l'avvio di un'applicazione",
                code=INTERPRETER_INLINE_CODE_FORBIDDEN,
            )


def authorize(command: object, args: list[str] | None = None) -> tuple[str, list[str]]:
    """Autorizza un avvio, o solleva `ProcessLaunchRejected`.

    Restituisce `(percorso assoluto autorizzato, argomenti validati)`. Il
    chiamante deve eseguire **quel** percorso: è l'unico modo perché il file su
    cui è stata presa la decisione e quello che parte siano lo stesso.
    """
    cleaned_args = _validate_args(args)
    exe = _resolve_executable(command)
    _refuse_volatile_location(exe)
    _refuse_inline_code(exe, cleaned_args)
    return str(exe), cleaned_args


def is_inline_code_interpreter(name: str) -> bool:
    """Esposto per i test e la diagnostica: questo nome è un interprete noto?"""
    return _interpreter_stem(name) in _INLINE_CODE_FLAGS


__all__ = [
    "ProcessLaunchRejected",
    "authorize",
    "is_inline_code_interpreter",
    "ARGS_INVALID",
    "EXECUTABLE_LOCATION_FORBIDDEN",
    "EXECUTABLE_NOT_FOUND",
    "EXECUTABLE_PATH_INVALID",
    "INTERPRETER_INLINE_CODE_FORBIDDEN",
    "MAX_ARGS",
    "MAX_ARG_LEN",
]
