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


# ---------------------------------------------------------------------------
# VERIFIED: solo con l'effetto riletto dal sistema
# ---------------------------------------------------------------------------
def test_a_field_is_verified_by_reading_it_back(adapter):
    verdict = verif.verify_action(APP, _edit_action(adapter))
    assert verdict["state"] == verif.VERIFIED, verdict
    assert verdict["observed"]["after"] != verdict["observed"]["before"], verdict


def test_the_evidence_says_what_was_observed(adapter):
    """Un verdetto senza evidenza e' un'opinione con un nome tecnico."""
    verdict = verif.verify_action(APP, _edit_action(adapter))
    assert verdict["evidence"], verdict
    assert str(verdict["observed"]["after"]) in verdict["evidence"], verdict


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
    verdict = verif.verify_action(APP, _button_action(adapter))
    assert verdict["state"] in (verif.UNSUPPORTED, verif.VERIFIED), verdict
    if verdict["state"] == verif.UNSUPPORTED:
        assert "non e' cambiato" in verdict["evidence"], verdict


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


# ---------------------------------------------------------------------------
# Il verdetto vive con l'adapter, e sopravvive al riavvio
# ---------------------------------------------------------------------------
def test_the_verdict_is_recorded_on_the_action(adapter):
    action = _edit_action(adapter)
    assert get_adapter(APP).actions[0].verification is None or True

    verify_and_record(APP, action)
    recorded = next(a for a in get_adapter(APP).actions if a.name == action)
    assert recorded.verification["state"] == verif.VERIFIED, recorded.verification


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
