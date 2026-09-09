r"""La policy sandbox si aggirava da tre rotte REST. Non piu'.

Il difetto, misurato su `main` prima di questa patch
-----------------------------------------------------
`check_action` sta dentro `invoke_action`, quindi ogni superficie che passa
dall'engine e' coperta — e c'e' gia' un test a runtime per ciascuna
(`test_sandbox_enforcement.py`, `test_agent_execution_gate.py`). Ma tre rotte
agiscono sull'interfaccia senza passare di li'::

    api/rest/ui.py  ->  ui.accessible_click(name, role)
    api/rest/ui.py  ->  ui.accessible_set_text(name, text, role)
    api/rest/ui.py  ->  ui.click_text_vision(text, dry_run)

Con `SandboxPolicy(app_id="contoso-crm", denied_actions={"click_btn_save"})` in
vigore, la riproduzione dava::

    via invoke_action      ->  ok=False  denied=True  error='explicitly denied'
    via click_text_vision  ->  denied=None            error='text not found'

Nessuna policy consultata. Si era fermata solo perche' su uno schermo vuoto non
c'era testo da trovare, non perche' qualcosa l'avesse bloccata: con
un'applicazione vera davanti quel click parte. Negare `click_btn_save` non
impediva di premere lo stesso pulsante — bastava chiederlo per nome, o per il
testo che ci si legge sopra.

Cosa dimostrano questi test
---------------------------
Che le due porte sulla stessa stanza adesso hanno la stessa serratura, e che la
serratura non e' diventata un muro: cio' che la policy non nega passa ancora.

Il caso «schermo vuoto» e perche' conta
----------------------------------------
Il rifiuto arriva PRIMA che si guardi lo schermo. Un test che si accontentasse
di `ok is False` passerebbe anche sul vecchio codice, dove il risultato era
`ok=False` per «testo non trovato»: e' esattamente la trappola in cui il difetto
si era nascosto. Per questo ogni asserzione qui guarda `denied` e il codice, mai
il solo `ok`.
"""
from __future__ import annotations

import pytest

from windows_os_api.apps.adapters.engine import create_adapter, invoke_action, reset_adapters
from windows_os_api.apps.sandbox.permissions import SandboxPolicy, reset_policies, set_policy
from windows_os_api.apps.sandbox.ui_guard import UI_TARGET_DENIED, check_ui_target
from windows_os_api.apps.ui_inspector import service as ui
from windows_os_api.backends.factory import reset_backend
from windows_os_api.core.runtime.config import get_settings

pytestmark = pytest.mark.security

APP = "contoso-crm"


@pytest.fixture
def denied_button(tmp_sandbox):
    """Un pulsante vero dell'adapter CRM, negato dalla policy.

    Restituisce l'azione, cosi' i test possono puntare al controllo con tutti i
    nomi con cui e' raggiungibile.
    """
    get_settings.cache_clear()
    reset_backend()
    reset_adapters()
    reset_policies()
    adapter = create_adapter(APP, hwnd=1001)
    action = next(a for a in adapter.actions if a.control_type == "Button")
    set_policy(SandboxPolicy(app_id=APP, denied_actions={action.name}))
    yield action
    reset_policies()
    reset_adapters()


def _label(action) -> str:
    """Il nome «umano» del controllo: quello che un chiamante scriverebbe."""
    return (action.description or action.name).split()[-1]


# ---------------------------------------------------------------------------
# La premessa: il gate dell'engine funziona gia'
# ---------------------------------------------------------------------------
def test_the_engine_path_denies_as_it_always_did(denied_button):
    """Se questo fallisse, i test qui sotto non direbbero niente sul buco."""
    out = invoke_action(APP, denied_button.name, {})
    assert out["ok"] is False, out
    assert out["denied"] is True, out


# ---------------------------------------------------------------------------
# Le tre porte laterali
# ---------------------------------------------------------------------------
def test_click_text_vision_no_longer_walks_around_the_policy(denied_button):
    """Il caso esatto della riproduzione.

    `denied` e' l'asserzione che conta: sul vecchio codice questa chiamata
    tornava `ok=False` per «testo non trovato», quindi un test su `ok` sarebbe
    passato anche col difetto in piedi.
    """
    out = ui.click_text_vision(_label(denied_button), dry_run=True)
    assert out.get("denied") is True, out
    assert out["code"] == UI_TARGET_DENIED, out
    assert out["action"] == denied_button.name, out


def test_accessible_click_no_longer_walks_around_the_policy(denied_button):
    out = ui.accessible_click(_label(denied_button))
    assert out.get("denied") is True, out
    assert out["code"] == UI_TARGET_DENIED, out


def test_accessible_set_text_no_longer_walks_around_the_policy(denied_button):
    out = ui.accessible_set_text(_label(denied_button), "qualcosa")
    assert out.get("denied") is True, out
    assert out["code"] == UI_TARGET_DENIED, out


@pytest.mark.parametrize("spelling", ["automation_id", "name", "description"])
def test_the_control_is_refused_by_every_name_it_answers_to(denied_button, spelling):
    """Un divieto che si aggira riscrivendo il bersaglio non e' un divieto.

    `btn.save`, `click_btn_save` e `Click button Save` sono lo stesso pulsante:
    il confronto ignora maiuscole e punteggiatura apposta.
    """
    target = getattr(denied_button, spelling)
    out = ui.accessible_click(target)
    assert out.get("denied") is True, (spelling, target, out)


def test_the_refusal_names_the_policy_that_decided(denied_button):
    """«negato» non si corregge; «la policy nega click_btn_save» si'."""
    out = ui.accessible_click(_label(denied_button))
    assert denied_button.name in out["error"], out
    assert APP in out["error"], out


# ---------------------------------------------------------------------------
# Il gate non e' diventato un muro
# ---------------------------------------------------------------------------
def test_a_control_no_policy_denies_still_goes_through(denied_button):
    """Un'altra azione dello stesso adapter non e' toccata dal divieto."""
    other = "Cancella" if "Cancella" not in _label(denied_button) else "Chiudi"
    out = ui.click_text_vision(other, dry_run=True)
    assert out.get("denied") is None, out


def test_a_target_no_adapter_knows_is_not_invented_into_a_ban(denied_button):
    """Cio' che nessuna policy copriva prima non diventa vietato adesso.

    Vietare un bersaglio sconosciuto vorrebbe dire vietare mezza interfaccia.
    """
    assert check_ui_target("un-controllo-che-nessuno-ha-mai-visto")["allowed"] is True


def test_with_no_policy_in_force_nothing_is_refused(tmp_sandbox):
    get_settings.cache_clear()
    reset_backend()
    reset_adapters()
    reset_policies()
    adapter = create_adapter(APP, hwnd=1001)
    action = next(a for a in adapter.actions if a.control_type == "Button")
    assert check_ui_target(_label(action))["allowed"] is True
    reset_adapters()


def test_a_target_too_short_to_identify_anything_matches_nothing(denied_button):
    """Sotto i tre caratteri il confronto farebbe combaciare qualunque cosa."""
    assert check_ui_target("ok")["allowed"] is True
    assert check_ui_target("")["allowed"] is True
    assert check_ui_target(None)["allowed"] is True


# ---------------------------------------------------------------------------
# La superficie HTTP
# ---------------------------------------------------------------------------
def test_the_api_answers_403_on_every_one_of_the_three_routes(
    client, auth_headers, denied_button
):
    """Un rifiuto che risponde 200 e' un successo per chi legge lo status code."""
    label = _label(denied_button)
    calls = [
        ("/v1/ui/click", {"name": label}),
        ("/v1/ui/set-text", {"name": label, "text": "x"}),
        ("/v1/ui/vision/click", {"text": label, "dry_run": True}),
    ]
    for path, body in calls:
        response = client.post(path, headers=auth_headers, json=body)
        assert response.status_code == 403, (path, response.status_code, response.text)


def test_an_allowed_target_is_not_403(client, auth_headers, denied_button):
    """La controprova: senza questa, un gate che nega tutto passerebbe."""
    response = client.post(
        "/v1/ui/vision/click", headers=auth_headers,
        json={"text": "un-controllo-che-nessuno-ha-mai-visto", "dry_run": True},
    )
    assert response.status_code != 403, response.text
