r"""«Verificata» deve voler dire che se ne e' visto l'effetto.

Il problema che questo modulo risolve
--------------------------------------
Un adapter nasce da un'ispezione: si vede un pulsante nell'albero UI e si scrive
un'azione che lo preme. Questo dice che l'azione **esiste**, non che
**funziona** — la stessa distinzione della ricognizione stub e di
`supported=false` (#28).

Due cose che sembrano verifica e non lo sono, ed entrambe erano a portata di mano
qui dentro:

1. **la confidence di un modello** — `confidence = 0.95` e' una previsione su
   quanto probabilmente funzionera'; una previsione non e' un'osservazione;
2. **`ok: True` di `invoke_action`** — significa «ho agito», non «e' successo».
   Sul percorso Button quel valore e' **letterale**: viene restituito dopo il
   click qualunque cosa il click abbia prodotto.

Due difetti trovati costruendo questi test
-------------------------------------------
Non erano ipotesi: sono usciti scrivendo il verificatore.

* `FakeBackend.get_ui_tree` restituiva `dict(CRM_UI_TREE)` — una copia
  **superficiale**. I nodi figli erano gli stessi oggetti della costante di
  modulo, quindi chi scriveva `node["value"] = ...` modificava la fixture
  condivisa, per tutti i test successivi.
* `invoke_action`, sul ramo Edit, faceva esattamente quello: scriveva nel
  dizionario che aveva ricevuto. Non cambiava l'applicazione — sembrava
  funzionare solo perche' la copia era condivisa.

Messi insieme, un verificatore ingenuo avrebbe osservato **la propria
scrittura** e concluso «verificata» per il motivo sbagliato. Il test
`test_the_observation_is_not_the_verifier_looking_at_its_own_writing` esiste per
impedire che quella combinazione torni.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from windows_os_api.apps.adapters import verification as verif
from windows_os_api.apps.adapters.engine import (
    create_adapter,
    get_adapter,
    invoke_action,
    load_persisted_adapters,
    reset_adapters,
    verify_and_record,
)
from windows_os_api.apps.sandbox.permissions import SandboxPolicy, reset_policies, set_policy
from windows_os_api.backends import fake as fake_module
from windows_os_api.backends.factory import get_backend, reset_backend
from windows_os_api.core.runtime.config import get_settings

pytestmark = pytest.mark.security

APP = "contoso-crm"


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(tmp_path / "adapters"))
    get_settings.cache_clear()
    reset_backend()
    reset_adapters()
    reset_policies()
    yield create_adapter(APP, hwnd=1001)
    reset_policies()
    reset_adapters()


def _edit_action(adapter) -> str:
    return next(a.name for a in adapter.actions if a.control_type == "Edit")


def _button_action(adapter) -> str:
    return next(a.name for a in adapter.actions if a.control_type == "Button")


def test_unsafe_platform_ids_become_unique_route_safe_action_names():
    from windows_os_api.apps.adapters.engine import _default_actions_from_tree

    tree = {
        "children": [
            {"control_type": "Edit", "automation_id": "/a:b", "name": "first"},
            {"control_type": "Edit", "automation_id": "/a/b", "name": "second"},
        ]
    }
    names = [action.name for action in _default_actions_from_tree(tree)]

    assert len(names) == 2
    assert len(set(names)) == 2, names
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]+", name) for name in names)


# ---------------------------------------------------------------------------
# VERIFIED: solo con l'effetto riletto dal sistema
# ---------------------------------------------------------------------------
def test_a_field_is_verified_by_reading_it_back(adapter):
    backend = get_backend()
    action = next(a for a in adapter.actions if a.control_type == "Edit")
    from windows_os_api.apps.ui_inspector.service import find_by_automation_id

    original = find_by_automation_id(
        backend.get_ui_tree(1001), action.automation_id
    )["value"]
    verdict = verif.verify_action(APP, _edit_action(adapter))
    assert verdict["state"] == verif.VERIFIED, verdict
    assert verdict["observed"] == {
        "probe_observed": True,
        "rollback_observed": True,
    }, verdict
    restored = find_by_automation_id(
        backend.get_ui_tree(1001), action.automation_id
    )["value"]
    assert restored == original, "la verifica ha lasciato la sonda nel campo"


def test_the_evidence_says_what_was_observed(adapter):
    """Un verdetto senza evidenza e' un'opinione con un nome tecnico."""
    verdict = verif.verify_action(APP, _edit_action(adapter))
    assert verdict["evidence"], verdict
    assert "riletta" in verdict["evidence"], verdict
    assert verdict["observed"]["rollback_observed"] is True, verdict


def test_the_observation_is_not_the_verifier_looking_at_its_own_writing(adapter):
    """Il test che difende la correzione dei due difetti.

    Se `get_ui_tree` tornasse a restituire una copia superficiale, o se
    `invoke_action` tornasse a scrivere nel dizionario ricevuto, il verificatore
    rileggerebbe la propria scrittura. Qui si prova il contrario in modo
    diretto: si modifica l'albero RESTITUITO e si verifica che il backend non
    ne sappia niente.
    """
    backend = get_backend()
    action = next(a for a in adapter.actions if a.control_type == "Edit")

    handed_out = backend.get_ui_tree(1001)
    from windows_os_api.apps.ui_inspector.service import find_by_automation_id

    node = find_by_automation_id(handed_out, action.automation_id)
    node["value"] = "scritto-nella-copia"

    fresh = backend.get_ui_tree(1001)
    assert find_by_automation_id(fresh, action.automation_id)["value"] != (
        "scritto-nella-copia"
    ), "scrivere nell'albero restituito ha cambiato lo stato del backend"


def test_the_module_level_fixture_is_never_mutated(adapter):
    """La costante condivisa non deve portarsi dietro lo sporco fra i test.

    `copy.deepcopy` e non un riferimento: la prima stesura di questo test
    scriveva `before = fake_module.CRM_UI_TREE` e poi confrontava la costante
    con quel nome — cioe' un oggetto con se stesso. Passava sempre, anche col
    difetto in piedi. Un test che non puo' fallire e' peggio di nessun test.
    """
    import copy as _copy

    before = _copy.deepcopy(fake_module.CRM_UI_TREE)
    invoke_action(APP, _edit_action(adapter), {"value": "qualcosa-di-nuovo"})
    assert fake_module.CRM_UI_TREE == before, (
        "l'azione ha modificato la costante di modulo: lo stato perde fra i test"
    )


def test_invoking_really_changes_what_the_system_reports(adapter):
    """L'effetto passa dal backend, non da una scrittura su una copia."""
    action = next(a for a in adapter.actions if a.control_type == "Edit")
    invoke_action(APP, action.name, {"value": "Mario Rossi"})

    from windows_os_api.apps.ui_inspector.service import find_by_automation_id

    fresh = get_backend().get_ui_tree(1001)
    assert find_by_automation_id(fresh, action.automation_id)["value"] == "Mario Rossi"


# ---------------------------------------------------------------------------
# Gli altri verdetti, ognuno per la sua ragione
# ---------------------------------------------------------------------------
def test_a_denied_action_is_blocked_not_failed(adapter):
    """«Non ho potuto provarla» non e' «non funziona».

    Confonderli farebbe risultare rotta un'applicazione sana solo perche' la
    policy la protegge.
    """
    action = _edit_action(adapter)
    set_policy(SandboxPolicy(app_id=APP, denied_actions={action}))

    verdict = verif.verify_action(APP, action)
    assert verdict["state"] == verif.BLOCKED, verdict
    assert verdict["state"] != verif.FAILED
    assert "policy" in verdict["evidence"].lower(), verdict


def test_a_denied_action_is_not_tried_anyway(adapter):
    """Provare comunque sarebbe aggirare la policy per curiosita'."""
    action = next(a for a in adapter.actions if a.control_type == "Edit")
    set_policy(SandboxPolicy(app_id=APP, denied_actions={action.name}))

    from windows_os_api.apps.ui_inspector.service import find_by_automation_id

    before = find_by_automation_id(get_backend().get_ui_tree(1001), action.automation_id)
    verif.verify_action(APP, action.name)
    after = find_by_automation_id(get_backend().get_ui_tree(1001), action.automation_id)
    assert after["value"] == before["value"], "il campo e' cambiato: e' stata provata lo stesso"


def test_a_button_with_no_observable_effect_is_unsupported_not_verified(adapter):
    """Il verdetto onesto quando non si puo' guardare.

    Un click che non lascia traccia nell'albero UI non e' dimostrato: dire
    «verificata» perche' non ha sollevato eccezioni sarebbe tornare al problema
    che questo modulo esiste per risolvere.
    """
    backend = get_backend()
    before = list(backend._input_log)
    verdict = verif.verify_action(APP, _button_action(adapter))
    assert verdict["state"] == verif.UNSUPPORTED, verdict
    assert verdict["code"] == "EXPECTED_EFFECT_UNDEFINED", verdict
    assert verdict["attempted"] is False, verdict
    assert backend._input_log == before, "un bottone senza contratto e' stato premuto"


def test_an_invoke_failure_can_never_be_verified(adapter, monkeypatch):
    from windows_os_api.apps.adapters import engine

    real_invoke = engine.invoke_action
    calls = 0

    def fail_probe_then_allow_rollback(app_id, action_name, params=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"ok": False, "error": "injected failure"}
        return real_invoke(app_id, action_name, params)

    monkeypatch.setattr(engine, "invoke_action", fail_probe_then_allow_rollback)
    verdict = verif.verify_action(APP, _edit_action(adapter))

    assert verdict["state"] == verif.FAILED, verdict
    assert verdict["code"] == "INVOKE_FAILED", verdict
    assert verdict["observed"]["rollback_observed"] is True, verdict


def test_a_failed_rollback_invalidates_an_observed_probe(adapter, monkeypatch):
    from windows_os_api.apps.adapters import engine

    real_invoke = engine.invoke_action
    calls = 0

    def allow_probe_but_fail_rollback(app_id, action_name, params=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            return {"ok": False, "error": "injected rollback failure"}
        return real_invoke(app_id, action_name, params)

    monkeypatch.setattr(engine, "invoke_action", allow_probe_but_fail_rollback)
    verdict = verif.verify_action(APP, _edit_action(adapter))

    assert verdict["state"] == verif.FAILED, verdict
    assert verdict["code"] == "ROLLBACK_FAILED", verdict
    assert verdict["observed"]["rollback_observed"] is False, verdict


def test_an_observation_exception_still_rolls_back(adapter, monkeypatch):
    backend = get_backend()
    real_get_tree = backend.get_ui_tree
    calls = 0

    def fail_only_probe_readback(hwnd=None):
        nonlocal calls
        calls += 1
        # 1 original read, 2 invoke lookup, 3 probe readback.
        if calls == 3:
            raise RuntimeError("injected observation failure")
        return real_get_tree(hwnd)

    monkeypatch.setattr(backend, "get_ui_tree", fail_only_probe_readback)
    verdict = verif.verify_action(APP, _edit_action(adapter))

    assert verdict["state"] == verif.FAILED, verdict
    assert verdict["code"] == "VERIFICATION_EXCEPTION", verdict
    assert verdict["observed"]["rollback_observed"] is True, verdict


def test_concurrent_verifications_are_serialized_per_adapter(adapter, monkeypatch):
    from windows_os_api.apps.adapters import engine

    real_invoke = engine.invoke_action
    state_lock = threading.Lock()
    start = threading.Barrier(2)
    active = 0
    maximum = 0

    def slow_invoke(app_id, action_name, params=None):
        nonlocal active, maximum
        with state_lock:
            active += 1
            maximum = max(maximum, active)
        try:
            time.sleep(0.02)
            return real_invoke(app_id, action_name, params)
        finally:
            with state_lock:
                active -= 1

    def run_verification():
        start.wait(timeout=2)
        return verif.verify_action(APP, _edit_action(adapter))

    monkeypatch.setattr(engine, "invoke_action", slow_invoke)
    with ThreadPoolExecutor(max_workers=2) as pool:
        verdicts = list(pool.map(lambda _index: run_verification(), range(2)))

    assert all(v["state"] == verif.VERIFIED for v in verdicts), verdicts
    assert maximum == 1, "two probes modified the same adapter concurrently"


def test_an_unbound_adapter_is_blocked(adapter):
    """Dopo un riavvio non c'e' niente su cui provare l'azione."""
    action = _edit_action(adapter)
    reset_adapters()
    load_persisted_adapters()

    verdict = verif.verify_action(APP, action)
    assert verdict["state"] == verif.BLOCKED, verdict
    assert verdict["code"] == "ADAPTER_NOT_BOUND", verdict


def test_an_unknown_action_is_not_silently_verified(adapter):
    verdict = verif.verify_action(APP, "azione_che_non_esiste")
    assert verdict["state"] == verif.FAILED, verdict


def test_an_unknown_app_is_not_silently_verified(adapter):
    verdict = verif.verify_action("app-che-non-esiste", "qualsiasi")
    assert verdict["state"] == verif.FAILED, verdict


# ---------------------------------------------------------------------------
# UNSTABLE: una capability che funziona a volte
# ---------------------------------------------------------------------------
def test_repeated_agreement_keeps_the_verdict(adapter):
    verdict = verif.verify_action_repeatedly(APP, _edit_action(adapter), times=3)
    assert verdict["state"] == verif.VERIFIED, verdict
    assert verdict["attempts"] == 3, verdict


def test_disagreeing_attempts_are_unstable(adapter, monkeypatch):
    """Un solo tentativo riuscito non distingue «funziona» da «funziona a volte»."""
    action = _edit_action(adapter)
    verdicts = iter([
        {"state": verif.VERIFIED, "evidence": "ok"},
        {"state": verif.FAILED, "evidence": "no"},
    ])
    monkeypatch.setattr(verif, "verify_action", lambda *a, **k: next(verdicts))

    verdict = verif.verify_action_repeatedly(APP, action, times=2)
    assert verdict["state"] == verif.UNSTABLE, verdict
    assert sorted(verdict["states"]) == [verif.FAILED, verif.VERIFIED], verdict


def test_direct_call_cannot_request_unbounded_repetitions(adapter):
    action = _edit_action(adapter)
    before = next(a for a in get_adapter(APP).actions if a.name == action).verification

    result = verify_and_record(APP, action, times=11)

    assert result["ok"] is False, result
    assert result["recorded"] is False, result
    assert "between 1 and 10" in result["error"], result
    after = next(a for a in get_adapter(APP).actions if a.name == action).verification
    assert after is before, "an invalid request changed the persisted verdict"


# ---------------------------------------------------------------------------
# Il verdetto vive con l'adapter, e sopravvive al riavvio
# ---------------------------------------------------------------------------
def test_the_verdict_is_recorded_on_the_action(adapter):
    action = _edit_action(adapter)
    target = next(a for a in get_adapter(APP).actions if a.name == action)
    assert target.verification is None

    result = verify_and_record(APP, action)
    assert result["ok"] is True, result
    assert result["persisted"] is True, result
    recorded = next(a for a in get_adapter(APP).actions if a.name == action)
    assert recorded.verification["state"] == verif.VERIFIED, recorded.verification


def test_persisted_verdict_does_not_copy_the_original_field_value(adapter):
    secret = "customer-secret@example.invalid"
    action = _edit_action(adapter)
    changed = invoke_action(APP, action, {"value": secret})
    assert changed["ok"] is True, changed

    result = verify_and_record(APP, action)
    assert result["ok"] is True, result
    manifest = next(Path(os.environ["WINOS_ADAPTER_STORE"]).glob("*.json"))
    persisted = manifest.read_text(encoding="utf-8")

    assert secret not in json.dumps(result), "il risultato espone il valore originale"
    assert secret not in persisted, "il manifest persiste dati letti dall'app"


def test_the_verdict_survives_a_restart(adapter):
    """Al prossimo avvio si deve sapere cosa era gia' stato dimostrato."""
    action = _edit_action(adapter)
    verify_and_record(APP, action)

    reset_adapters()
    load_persisted_adapters()

    restored = next(a for a in get_adapter(APP).actions if a.name == action)
    assert restored.verification is not None, "il verdetto e' andato perso al riavvio"
    assert restored.verification["state"] == verif.VERIFIED, restored.verification
    assert restored.verification["evidence"], restored.verification


def test_an_action_nobody_tried_has_no_verdict(adapter):
    """`None` significa «nessuno ha guardato», non «non funziona»."""
    assert all(a.verification is None for a in get_adapter(APP).actions)


def test_recording_on_an_unknown_action_does_not_pretend(adapter):
    out = verify_and_record(APP, "azione_che_non_esiste")
    assert out["ok"] is False, out


# ---------------------------------------------------------------------------
# Il contratto degli stati
# ---------------------------------------------------------------------------
def test_every_state_the_module_can_return_is_declared(adapter):
    """Uno stato non dichiarato e' uno stato che nessun chiamante gestira'."""
    for produced in (
        verif.verify_action(APP, _edit_action(adapter))["state"],
        verif.verify_action(APP, _button_action(adapter))["state"],
        verif.verify_action(APP, "non_esiste")["state"],
    ):
        assert produced in verif.VERIFICATION_STATES, produced


def test_confidence_is_not_part_of_the_verdict():
    """La riga da non riattraversare mai.

    Se un giorno un verdetto cominciasse a dipendere da una confidence, sarebbe
    tornata la previsione al posto dell'osservazione.
    """
    import inspect

    source = inspect.getsource(verif)
    body = source.split('"""', 2)[-1]  # esclude la docstring del modulo, che ne parla
    assert "confidence" not in body.lower(), (
        "il verdetto guarda una confidence: una previsione non e' un'osservazione"
    )
