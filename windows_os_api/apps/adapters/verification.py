r"""Una capability e' verificata quando se ne e' OSSERVATO l'effetto. Mai prima.

Perche' esiste questo modulo
-----------------------------
Un adapter nasce da un'ispezione: si guarda l'albero UI, si vede un pulsante, si
scrive un'azione che lo preme. Questo dice che l'azione **esiste**, non che
**funziona**. La distinzione e' la stessa di `supported=false` (#28) e della
ricognizione degli stub: un metodo che c'e' non e' una capability dimostrata.

Cosa NON conta come verifica
-----------------------------
1. **La confidence di un modello.** `confidence = 0.95` e' una previsione su
   quanto probabilmente funzionera'. Una previsione non e' un'osservazione.
2. **`ok: True` di `invoke_action`.** Significa «ho agito», non «e' successo»:
   sul percorso Button il valore e' letterale, restituito dopo il click
   qualunque cosa il click abbia prodotto.

Quindi il verdetto qui si forma in un modo solo: si legge lo stato PRIMA, si
esegue l'azione, si rilegge lo stato DOPO **dal sistema**, e si guarda se e'
cambiato come doveva.

Gli stati, e perche' sono distinti
-----------------------------------
``DISCOVERED``   l'azione esiste nell'adapter; nessuno l'ha provata.
``VERIFIED``     eseguita, e l'effetto atteso e' stato osservato.
``FAILED``       eseguita, e l'effetto atteso NON e' comparso.
``BLOCKED``      la policy sandbox la nega: non e' stato possibile provarla.
                 Diverso da FAILED — «non ho potuto» non e' «non funziona», e
                 confonderli farebbe risultare rotta un'applicazione sana.
``UNSUPPORTED``  non c'e' un effetto osservabile definito per questo tipo di
                 controllo su questo backend. Cioe': non lo sappiamo, e lo
                 diciamo. E' il verdetto onesto per un pulsante il cui effetto
                 non compare nell'albero UI.
``UNSTABLE``     provata piu' volte con esiti diversi. Una capability che
                 funziona a volte non e' una capability su cui costruire.

Cosa e' osservabile, oggi
--------------------------
Un campo di testo: si scrive un valore e si rilegge quel campo. E' un'osservazione
completa — il confronto e' con cio' che si e' chiesto.

Un pulsante o una voce di menu: l'effetto e' dell'applicazione, e puo' non
comparire da nessuna parte nell'albero. Quando l'albero non cambia il verdetto e'
``UNSUPPORTED``, non ``VERIFIED``: dire «verificata» perche' il click non ha
sollevato eccezioni sarebbe tornare esattamente al problema che questo modulo
esiste per risolvere.
"""
from __future__ import annotations

import time
from typing import Any

DISCOVERED = "DISCOVERED"
VERIFIED = "VERIFIED"
FAILED = "FAILED"
BLOCKED = "BLOCKED"
UNSUPPORTED = "UNSUPPORTED"
UNSTABLE = "UNSTABLE"

VERIFICATION_STATES = frozenset(
    {DISCOVERED, VERIFIED, FAILED, BLOCKED, UNSUPPORTED, UNSTABLE}
)

# Il valore scritto durante la prova. Riconoscibile, cosi' se resta in giro si
# capisce da dove viene, e diverso a ogni giro, cosi' un campo che conteneva gia'
# quel testo non fa sembrare riuscita una scrittura che non e' avvenuta.
_PROBE_PREFIX = "winos-verify-"


def _probe_value() -> str:
    return f"{_PROBE_PREFIX}{int(time.time() * 1000) % 1_000_000}"


def _node_value(tree: dict[str, Any], automation_id: str) -> Any:
    from windows_os_api.apps.ui_inspector.service import find_by_automation_id

    node = find_by_automation_id(tree, automation_id)
    return None if node is None else node.get("value")


def _verdict(state: str, evidence: str, **extra: Any) -> dict[str, Any]:
    return {
        "state": state,
        "evidence": evidence,
        "checked_at": time.time(),
        **extra,
    }


def verify_action(app_id: str, action_name: str) -> dict[str, Any]:
    """Prova un'azione e restituisce il verdetto, con l'evidenza che lo sostiene.

    L'evidenza e' testo leggibile: chi legge il verdetto deve poter capire *cosa*
    e' stato osservato, non solo che qualcuno ha deciso.
    """
    from windows_os_api.apps.adapters.engine import (
        ADAPTER_NOT_BOUND,
        get_adapter,
        invoke_action,
    )
    from windows_os_api.apps.sandbox.permissions import check_action
    from windows_os_api.backends.factory import get_backend

    adapter = get_adapter(app_id)
    if adapter is None:
        return _verdict(FAILED, f"nessun adapter registrato per {app_id!r}")

    action = next((a for a in adapter.actions if a.name == action_name), None)
    if action is None:
        return _verdict(FAILED, f"l'adapter {app_id!r} non ha l'azione {action_name!r}")

    if not adapter.bound:
        return _verdict(
            BLOCKED,
            "l'adapter e' stato ricaricato da disco e non e' agganciato a una "
            "finestra: non c'e' niente su cui provare l'azione",
            code=ADAPTER_NOT_BOUND,
        )

    # La policy si consulta PRIMA di agire: se nega, non si e' provato nulla, e
    # il verdetto lo dice. Provare comunque sarebbe aggirare la policy per
    # curiosita'.
    gate = check_action(app_id, action_name, action.risk)
    if not gate.get("allowed"):
        return _verdict(
            BLOCKED,
            f"la policy sandbox nega {action_name!r}: non e' stato possibile provarla "
            f"({gate.get('reason')})",
        )

    backend = get_backend()

    if action.control_type == "Edit":
        probe = _probe_value()
        before = _node_value(backend.get_ui_tree(adapter.hwnd), action.automation_id)
        result = invoke_action(app_id, action_name, {"value": probe})
        after = _node_value(backend.get_ui_tree(adapter.hwnd), action.automation_id)

        if after == probe:
            return _verdict(
                VERIFIED,
                f"scritto {probe!r} in {action.automation_id!r} e riletto dal "
                f"sistema: il campo conteneva {before!r}, adesso contiene {after!r}",
                observed={"before": before, "after": after},
            )
        return _verdict(
            FAILED,
            f"scritto {probe!r} in {action.automation_id!r}, ma rileggendo il "
            f"campo contiene {after!r}: l'effetto atteso non e' comparso",
            observed={"before": before, "after": after},
            invoke_ok=result.get("ok"),
        )

    if action.control_type in ("Button", "MenuItem"):
        before = backend.get_ui_tree(adapter.hwnd)
        invoke_action(app_id, action_name, {})
        after = backend.get_ui_tree(adapter.hwnd)
        if after != before:
            return _verdict(
                VERIFIED,
                f"dopo {action_name!r} l'albero UI e' cambiato: l'effetto e' "
                f"osservabile dal sistema",
            )
        # L'albero identico non prova che il click non abbia fatto niente: prova
        # che, se ha fatto qualcosa, quel qualcosa non si vede da qui.
        return _verdict(
            UNSUPPORTED,
            f"{action_name!r} e' stata eseguita, ma l'albero UI non e' cambiato: "
            f"non esiste un effetto osservabile da questo backend, quindi non si "
            f"puo' dire che funzioni",
        )

    return _verdict(
        UNSUPPORTED,
        f"nessuna osservazione definita per un controllo di tipo "
        f"{action.control_type!r}",
    )


def verify_action_repeatedly(app_id: str, action_name: str, times: int = 2) -> dict[str, Any]:
    """Ripete la prova: esiti diversi valgono `UNSTABLE`.

    Una capability che funziona a volte non e' una capability su cui costruire,
    e un solo tentativo riuscito non distingue le due cose.
    """
    if times < 1:
        return _verdict(FAILED, "numero di ripetizioni non valido")

    verdicts = [verify_action(app_id, action_name) for _ in range(times)]
    states = {v["state"] for v in verdicts}
    if len(states) == 1:
        final = dict(verdicts[-1])
        final["attempts"] = times
        return final
    return _verdict(
        UNSTABLE,
        f"{times} tentativi hanno dato esiti diversi ({', '.join(sorted(states))}): "
        f"non e' una capability su cui contare",
        attempts=times,
        states=sorted(states),
    )
