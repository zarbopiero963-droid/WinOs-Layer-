"""Quali servizi puo' controllare questa API? Solo quelli scritti qui.

Decisione owner D1-B, issue #6 (2026-09-08):

    control_service
          |
       allowlist
          |
    servizio autorizzato?
       |-- SI  -> continua
       \\-- NO  -> BLOCK

e, testualmente: «l'allowlist puo' partire vuota: default-deny. Nessun servizio
deve essere controllabile se non esplicitamente autorizzato. Non usare
ADMIN + flag come autorizzazione implicita globale.»

Cosa c'era prima
----------------
`control_service` sanificava il nome dell'unit — niente metacaratteri di shell,
niente path traversal — e poi lo passava a `systemctl`. Quella sanificazione
impedisce di **iniettare** un comando, non di **fermare il servizio sbagliato**:
`stop` su `ssh`, `firewalld` o `systemd-journald` sono tutti nomi di unit
perfettamente validi. L'unica cosa che si frapponeva erano i permessi di
systemd, cioe' una difesa che sta fuori da questo programma e che su una
macchina dove il processo gira da root non c'e'.

Default-deny, e perche' e' la scelta giusta
--------------------------------------------
Senza `WINOS_SERVICE_ALLOWLIST` l'insieme e' **vuoto** e ogni azione e'
rifiutata. Un endpoint che non fa niente finche' qualcuno non decide cosa puo'
fare e' scomodo; un endpoint che ferma qualunque servizio perche' nessuno ha
ancora deciso il contrario e' pericoloso. La scomodita' e' recuperabile, il
danno no.

Nomi esatti, niente glob
------------------------
`nginx` autorizza `nginx`, non `nginx-proxy` ne' `nginx*`. I glob sono il modo
in cui un'allowlist diventa permissiva senza che nessuno se ne accorga: un
`systemd-*` scritto per comodita' autorizza `systemd-journald`, e a quel punto
la lista non dice piu' cosa e' permesso.

Il ruolo ADMIN non e' una scorciatoia
--------------------------------------
L'allowlist e' controllata **prima e indipendentemente** dal ruolo del
chiamante. Un amministratore che chiede un servizio non autorizzato riceve lo
stesso rifiuto di chiunque altro: se ADMIN bastasse, l'allowlist sarebbe un
suggerimento e non un confine — che e' esattamente cio' che l'owner ha escluso.
"""
from __future__ import annotations

import os
from typing import Any

ENV_VAR = "WINOS_SERVICE_ALLOWLIST"

SERVICE_NOT_ALLOWED = "SERVICE_NOT_ALLOWED"
ALLOWLIST_EMPTY = "SERVICE_ALLOWLIST_EMPTY"

# Azioni che modificano lo stato di un servizio. `status` legge soltanto, ma
# passa dallo stesso endpoint e dallo stesso permesso: e' gated anche lui, per
# fail-closed. Aprirlo e' una decisione dell'owner, non un dettaglio che decido
# io scrivendo il gate.
CONTROL_ACTIONS = ("start", "stop", "restart", "status", "enable", "disable")


class ServiceRejected(Exception):
    """Il servizio non e' autorizzato. Porta con se' il motivo e il codice."""

    def __init__(self, message: str, code: str = SERVICE_NOT_ALLOWED) -> None:
        super().__init__(message)
        self.code = code


def _canonical(name: str) -> str:
    """`nginx` e `nginx.service` sono lo stesso servizio.

    Normalizzato in un punto solo, cosi' la lista e la richiesta non possono
    divergere per un suffisso — e nessuno autorizza `nginx` credendo di aver
    bloccato `nginx.service`.
    """
    return name.strip().removesuffix(".service")


def allowed_services() -> frozenset[str]:
    """L'insieme autorizzato, letto adesso dall'ambiente.

    Letto a ogni chiamata e non all'import: un'allowlist congelata al primo
    import sarebbe invisibilmente diversa da quella che l'operatore crede di
    aver impostato dopo l'avvio.

    Il confronto e' **case-sensitive**: i nomi delle unit systemd lo sono, e
    un confronto tollerante autorizzerebbe `Nginx` a chi ha scritto `nginx` —
    cioe' un'unit diversa su un sistema case-sensitive.
    """
    raw = os.environ.get(ENV_VAR, "")
    return frozenset(
        _canonical(part) for part in raw.split(",") if _canonical(part)
    )


def check(name: object, action: str) -> str:
    """Autorizza una richiesta di controllo, o solleva `ServiceRejected`.

    Restituisce il nome canonico da usare: chi chiama non deve ri-normalizzare,
    perche' due normalizzazioni sono due occasioni di divergere.
    """
    if not isinstance(name, str) or not name.strip():
        raise ServiceRejected(
            "nessun servizio indicato: la richiesta non dice su cosa agire"
        )

    canonical = _canonical(name)
    allowed = allowed_services()

    if not allowed:
        # Distinto dal rifiuto normale di proposito: «la lista e' vuota» dice
        # all'operatore che deve configurare qualcosa, mentre «non e' in lista»
        # gli farebbe cercare un errore di battitura in una lista che non c'e'.
        raise ServiceRejected(
            f"nessun servizio e' autorizzato: {ENV_VAR} non e' impostato, "
            f"quindi il controllo dei servizi e' disabilitato (default-deny)",
            code=ALLOWLIST_EMPTY,
        )

    if canonical not in allowed:
        raise ServiceRejected(
            f"il servizio {canonical!r} non e' in {ENV_VAR}. "
            f"Autorizzati: {', '.join(sorted(allowed))}"
        )

    return canonical


def rejection(exc: ServiceRejected, name: str, action: str) -> dict[str, Any]:
    """La forma del rifiuto, uguale a quella che gia' usa il resto del modulo."""
    return {
        "ok": False,
        "denied": True,
        "code": exc.code,
        "error": str(exc),
        "name": name,
        "action": action,
    }
