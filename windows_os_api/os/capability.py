"""«Nessuno» e «non ho guardato» sono risposte diverse.

Decisione owner D3-A, issue #6 (2026-09-08).

Il problema, misurato: `GET /v1/printers` rispondeva `{"printers": []}` in
**quattro** situazioni diverse, e il chiamante non poteva distinguerle.

    A) su questa macchina non ci sono stampanti
    B) questo backend non implementa le stampanti
    C) la discovery non e' stata eseguita
    D) la discovery e' stata eseguita ed e' fallita

Una sola di queste (A) e' la risposta che il chiamante crede di leggere. Nelle
altre tre l'API sta dicendo «zero» quando dovrebbe dire «non lo so», ed e' la
stessa classe di difetto di `ok: true` su una finestra inesistente (#24) e di
`state: null` presentato come `verified: true` (#18).

Il contratto
------------
Additivo: la chiave dei dati resta quella di prima (`services`, `devices`,
`printers`, ...), cosi' i client esistenti non si accorgono di niente. Accanto
compaiono `supported` e, quando serve, `error_code`.

    supportata, con risultati   {"supported": true,  "printers": [...]}
    supportata, nessun elemento {"supported": true,  "printers": []}
    non supportata              {"supported": false, "printers": [],
                                 "error_code": "CAPABILITY_NOT_SUPPORTED"}
    discovery fallita           {"supported": true,  "printers": [],
                                 "error_code": "DISCOVERY_FAILED"}

Perche' `DiscoveryFailed` e' un'eccezione e non un `return []`
--------------------------------------------------------------
Prima i backend inghiottivano i propri errori e restituivano `[]`. Con quel
disegno il caso «discovery fallita» sarebbe **irraggiungibile**: si potrebbe
scrivere un test solo iniettando il fallimento in questo modulo, cioe'
testando il test. Perche' quel caso esista davvero, il backend deve poterlo
dire — e questa eccezione e' il modo in cui lo dice.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

CAPABILITY_NOT_SUPPORTED = "CAPABILITY_NOT_SUPPORTED"
CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
DISCOVERY_FAILED = "DISCOVERY_FAILED"

# «Questo backend non lo implementa» e «lo implementa ma qui manca lo strumento»
# sono due risposte diverse, e portano a due azioni diverse: la prima non
# cambiera' installando qualcosa, la seconda si'.
#
#   Windows + audio     -> NOT_SUPPORTED: servirebbe pycaw, che l'owner ha
#                          deciso di non aggiungere adesso (D4-B). Installare
#                          qualcosa sulla macchina non cambia niente.
#   Linux + printers    -> UNAVAILABLE: le stampanti sono implementate via
#                          lpstat, che su QUESTA macchina non c'e'. Installare
#                          CUPS le fa comparire.
_NOT_IMPLEMENTED_ATTR = "NOT_IMPLEMENTED"


class DiscoveryFailed(RuntimeError):
    """La capability c'e', ma QUESTO tentativo di enumerare e' fallito.

    Distinta da «non supportata» di proposito: un `lpstat` che va in timeout
    non dice nulla sull'esistenza delle stampanti, e rispondere `[]` a un
    timeout significherebbe affermare che non ce ne sono.
    """


def _flags(backend: Any) -> dict[str, bool]:
    getter = getattr(backend, "capability_flags", None)
    if callable(getter):
        try:
            return dict(getter())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _why_unsupported(backend: Any, flag: str) -> tuple[str, str]:
    """Distingue «non implementato» da «implementato ma non disponibile qui».

    Il backend dichiara in `NOT_IMPLEMENTED` le capability che non implementa
    affatto. Tutto il resto che risulta `False` e' implementato ma privo dello
    strumento necessario su questa macchina — e la differenza conta per chi
    legge: una si risolve installando qualcosa, l'altra no.
    """
    name = getattr(backend, "name", "?")
    never = getattr(backend, _NOT_IMPLEMENTED_ATTR, frozenset())
    try:
        not_implemented = flag in never
    except TypeError:
        not_implemented = False
    if not_implemented:
        return (
            CAPABILITY_NOT_SUPPORTED,
            f"il backend {name!r} non implementa {flag!r}: non e' una questione "
            f"di configurazione di questa macchina",
        )
    return (
        CAPABILITY_UNAVAILABLE,
        f"{flag!r} e' implementato sul backend {name!r}, ma su questa macchina "
        f"manca cio' che serve per usarlo",
    )


def discover(
    backend: Any,
    flag: str,
    key: str,
    fn: Callable[[], Iterable[dict[str, Any]]],
) -> dict[str, Any]:
    """Esegue una discovery e la incarta nel contratto.

    `flag` e' il nome della capability nella tabella del backend; `key` e' la
    chiave sotto cui i dati sono sempre stati esposti e che NON cambia.

    Una capability che il backend non dichiara affatto e' trattata come
    supportata: l'assenza dalla tabella significa «questo backend non tiene
    quel flag», non «la capability manca», e dedurre `false` da un'omissione
    sarebbe la stessa invenzione che questo modulo esiste per togliere.
    """
    flags = _flags(backend)
    if flags.get(flag) is False:
        code, reason = _why_unsupported(backend, flag)
        return {"supported": False, key: [], "error_code": code, "reason": reason}

    try:
        items = list(fn())
    except DiscoveryFailed as exc:
        return {
            "supported": True,
            key: [],
            "error_code": DISCOVERY_FAILED,
            "reason": str(exc),
        }

    return {"supported": True, key: items}


def unsupported(backend: Any, flag: str, key: str) -> dict[str, Any]:
    """La forma «non supportata» per chi non enumera una lista.

    Serve a `GET /v1/audio/volume`, che restituisce un valore singolo e non una
    lista: senza questo risponderebbe `{"volume": null, "muted": null}`, cioe'
    «il volume e' nullo» invece di «non so leggere il volume qui».
    """
    code, reason = _why_unsupported(backend, flag)
    return {"supported": False, key: None, "error_code": code, "reason": reason}
