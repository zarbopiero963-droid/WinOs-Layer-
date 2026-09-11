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
import uuid
from typing import Any

DISCOVERED = "DISCOVERED"
VERIFIED = "VERIFIED"
FAILED = "FAILED"
BLOCKED = "BLOCKED"
UNSUPPORTED = "UNSUPPORTED"
UNSTABLE = "UNSTABLE"
MAX_VERIFICATION_ATTEMPTS = 10

VERIFICATION_STATES = frozenset(
    {DISCOVERED, VERIFIED, FAILED, BLOCKED, UNSUPPORTED, UNSTABLE}
)

# Il valore scritto durante la prova. Riconoscibile, cosi' se resta in giro si
# capisce da dove viene, e diverso a ogni giro, cosi' un campo che conteneva gia'
# quel testo non fa sembrare riuscita una scrittura che non e' avvenuta.
_PROBE_PREFIX = "winos-verify-"


def _probe_value() -> str:
    return f"{_PROBE_PREFIX}{uuid.uuid4().hex}"


def _node(tree: dict[str, Any], automation_id: str) -> dict[str, Any] | None:
    from windows_os_api.apps.ui_inspector.service import find_by_automation_id

    return find_by_automation_id(tree, automation_id)


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
    from windows_os_api.apps.adapters.engine import get_adapter

    adapter = get_adapter(app_id)
    if adapter is None:
        return _verdict(FAILED, f"nessun adapter registrato per {app_id!r}")

    with adapter._verification_lock:
        return _verify_action_locked(adapter, action_name)


def _verify_action_locked(adapter: Any, action_name: str) -> dict[str, Any]:
    from windows_os_api.apps.adapters.engine import ADAPTER_NOT_BOUND, invoke_action
    from windows_os_api.apps.sandbox.permissions import check_action
    from windows_os_api.backends.factory import get_backend

    app_id = adapter.app_id

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
        try:
            original_node = _node(
                backend.get_ui_tree(adapter.hwnd), action.automation_id
            )
        except Exception as exc:  # noqa: BLE001
            return _verdict(
                FAILED,
                "impossibile leggere il valore originale prima della prova",
                code="OBSERVATION_FAILED",
                error_type=type(exc).__name__,
            )
        if original_node is None:
            return _verdict(
                FAILED,
                "il controllo da verificare non e' presente nell'albero UI corrente",
                code="ELEMENT_NOT_FOUND",
            )
        if "value" not in original_node or original_node.get("value") is None:
            # Senza una fotografia ripristinabile non si scrive: una verifica
            # non deve trasformarsi in una modifica irreversibile dell'app.
            return _verdict(
                UNSUPPORTED,
                "il backend non espone un valore originale ripristinabile per il campo",
                code="ORIGINAL_VALUE_UNAVAILABLE",
            )

        original = str(original_node["value"])
        probe = _probe_value()
        # UUID4 rende gia' la collisione trascurabile; il confronto esplicito
        # impedisce comunque che una no-op possa mai sembrare una scrittura.
        while probe == original:
            probe = _probe_value()

        candidate = _verdict(
            FAILED,
            "la prova non ha prodotto un effetto osservabile",
            code="EFFECT_NOT_OBSERVED",
            invoke_ok=False,
        )
        attempted = False
        try:
            attempted = True
            result = invoke_action(app_id, action_name, {"value": probe})
            if not result.get("ok"):
                candidate = _verdict(
                    FAILED,
                    "l'invocazione della sonda e' fallita",
                    code="INVOKE_FAILED",
                    invoke_ok=False,
                )
            else:
                observed_node = _node(
                    backend.get_ui_tree(adapter.hwnd), action.automation_id
                )
                probe_observed = bool(
                    observed_node is not None
                    and observed_node.get("value") == probe
                )
                if probe_observed:
                    candidate = _verdict(
                        VERIFIED,
                        "la sonda univoca e' stata riletta dal sistema; il valore "
                        "originale e' stato poi ripristinato e riletto",
                        observed={"probe_observed": True},
                        invoke_ok=True,
                    )
                else:
                    candidate = _verdict(
                        FAILED,
                        "l'invocazione ha risposto ok, ma la sonda non e' stata "
                        "riletta dal sistema",
                        code="EFFECT_NOT_OBSERVED",
                        observed={"probe_observed": False},
                        invoke_ok=True,
                    )
        except Exception as exc:  # noqa: BLE001
            candidate = _verdict(
                FAILED,
                "la prova ha sollevato un'eccezione; e' stato tentato il ripristino",
                code="VERIFICATION_EXCEPTION",
                error_type=type(exc).__name__,
                invoke_ok=False,
            )
        finally:
            if attempted:
                rollback_ok = False
                rollback_error_type = None
                try:
                    rollback = invoke_action(
                        app_id, action_name, {"value": original}
                    )
                    restored_node = _node(
                        backend.get_ui_tree(adapter.hwnd), action.automation_id
                    )
                    rollback_ok = bool(
                        rollback.get("ok")
                        and restored_node is not None
                        and str(restored_node.get("value")) == original
                    )
                except Exception as exc:  # noqa: BLE001
                    rollback_error_type = type(exc).__name__

                if not rollback_ok:
                    return _verdict(
                        FAILED,
                        "il valore originale non e' stato ripristinato e riletto; "
                        "la capability non puo' essere dichiarata verificata",
                        code="ROLLBACK_FAILED",
                        observed={"rollback_observed": False},
                        error_type=rollback_error_type,
                    )

        candidate.setdefault("observed", {})["rollback_observed"] = True
        return candidate

    if action.control_type in ("Button", "MenuItem"):
        # Un cambiamento qualunque dell'intero albero non e' un contratto di
        # effetto: potrebbe essere un orologio, una notifica o un'altra finestra.
        # Finche' l'action non dichiara quale stato preciso osservare, non la si
        # invoca neppure: un click esplorativo puo' essere irreversibile.
        return _verdict(
            UNSUPPORTED,
            f"{action_name!r} non dichiara un effetto specifico e osservabile; "
            "un cambiamento generico dell'albero UI non prova questa azione",
            code="EXPECTED_EFFECT_UNDEFINED",
            attempted=False,
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
    if times < 1 or times > MAX_VERIFICATION_ATTEMPTS:
        return _verdict(
            FAILED,
            f"il numero di tentativi deve essere fra 1 e {MAX_VERIFICATION_ATTEMPTS}",
            code="INVALID_ATTEMPTS",
        )

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
