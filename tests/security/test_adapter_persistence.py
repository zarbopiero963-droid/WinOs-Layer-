r"""Un adapter sopravvive al riavvio. Il suo legame con la finestra no.

Cosa c'era prima
----------------
`_adapters` e' un dizionario di processo: l'ispezione che costruisce un adapter
— quali azioni esistono, su quali `automation_id` — spariva alla chiusura del
runtime e andava rifatta da capo.

Il punto che questi test difendono
-----------------------------------
Di un adapter ci sono due cose con durata diversa:

* la **descrizione** vale finche' l'applicazione non cambia interfaccia;
* il **legame** — l'`hwnd` — e' un numero che il sistema operativo riassegna.

Salvare l'`hwnd` e ricaricarlo come valido significherebbe costruire un adapter
che clicca su una finestra che non e' quella che crede, **potenzialmente di
un'altra applicazione**. Per questo un adapter ricaricato torna NON agganciato,
e `invoke_action` lo rifiuta.

E' la stessa idea di `supported=false` (#28): dichiarare di non poter fare una
cosa e' sicuro; fingere di poterla fare non lo e'.

Come si simula un riavvio
--------------------------
`reset_adapters()` svuota la memoria di processo senza toccare il disco: e'
esattamente cio' che succede quando il runtime riparte. Poi
`load_persisted_adapters()` e' il passo di avvio.
"""
from __future__ import annotations

import json

import pytest

from windows_os_api.apps.adapters import store
from windows_os_api.apps.adapters.engine import (
    ADAPTER_NOT_BOUND,
    create_adapter,
    get_adapter,
    invoke_action,
    list_adapters,
    load_persisted_adapters,
    rebind_adapter,
    reset_adapters,
)
from windows_os_api.backends.factory import reset_backend
from windows_os_api.core.runtime.config import get_settings

pytestmark = pytest.mark.security

APP = "contoso-crm"


@pytest.fixture
def clean_store(tmp_path, monkeypatch):
    """Uno store vuoto per ogni test, e la memoria di processo pulita."""
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(tmp_path / "adapters"))
    get_settings.cache_clear()
    reset_backend()
    reset_adapters()
    yield tmp_path / "adapters"
    reset_adapters()


def _restart() -> dict:
    """Il runtime riparte: la memoria si svuota, il disco resta."""
    reset_adapters()
    return load_persisted_adapters()


# ---------------------------------------------------------------------------
# Sopravvivere al riavvio
# ---------------------------------------------------------------------------
def test_an_adapter_is_written_to_disk_when_created(clean_store):
    adapter = create_adapter(APP, hwnd=1001)
    assert adapter.persisted is True, adapter.persist_error
    files = list(clean_store.glob("*.json"))
    assert len(files) == 1, [p.name for p in files]


def test_the_adapter_is_still_there_after_a_restart(clean_store):
    """La regressione diretta: prima del manifest, qui non restava niente."""
    created = create_adapter(APP, hwnd=1001)
    assert created.actions, "l'adapter di partenza non ha azioni: test inutile"

    report = _restart()

    assert get_adapter(APP) is None or True  # leggibilita': l'asserzione vera e' sotto
    restored = get_adapter(APP)
    assert restored is not None, f"l'adapter non e' tornato: {report}"
    assert report["restored"] == [APP], report
    assert [a.name for a in restored.actions] == [a.name for a in created.actions]


def test_every_field_of_an_action_survives_the_round_trip(clean_store):
    """Un'azione che torna a meta' e' peggio di una che non torna."""
    created = create_adapter(APP, hwnd=1001)
    original = {a.name: a for a in created.actions}

    _restart()
    restored = {a.name: a for a in get_adapter(APP).actions}

    assert set(restored) == set(original)
    for name, action in original.items():
        back = restored[name]
        assert back.automation_id == action.automation_id, name
        assert back.control_type == action.control_type, name
        assert back.params == action.params, name
        assert back.risk == action.risk, name
        assert back.description == action.description, name


def test_the_openapi_is_regenerated_not_reloaded(clean_store):
    """Un documento derivato salvato accanto alla sorgente diverge dalla sorgente."""
    create_adapter(APP, hwnd=1001)
    manifest = json.loads(next(clean_store.glob("*.json")).read_text(encoding="utf-8"))
    assert "openapi" not in manifest, manifest.keys()

    _restart()
    assert get_adapter(APP).openapi, "l'openapi non e' stato rigenerato"


# ---------------------------------------------------------------------------
# Il legame con la finestra NON sopravvive — ed e' il punto
# ---------------------------------------------------------------------------
def test_a_reloaded_adapter_is_not_bound_to_any_window(clean_store):
    create_adapter(APP, hwnd=1001)
    _restart()

    restored = get_adapter(APP)
    assert restored.hwnd is None, (
        f"l'hwnd salvato e' tornato come valido ({restored.hwnd}): dopo un "
        f"riavvio quel numero puo' appartenere a un'altra applicazione"
    )
    assert restored.bound is False


def test_the_hwnd_is_never_written_to_the_manifest(clean_store):
    """Se non c'e' sul disco, nessuno potra' ricaricarlo per sbaglio domani."""
    create_adapter(APP, hwnd=1001)
    manifest = json.loads(next(clean_store.glob("*.json")).read_text(encoding="utf-8"))
    assert "hwnd" not in manifest, manifest


def test_invoking_an_unbound_adapter_is_refused_not_guessed(clean_store):
    """Il BLOCK che conta: nessun click su una finestra indovinata."""
    created = create_adapter(APP, hwnd=1001)
    action = created.actions[0].name
    _restart()

    out = invoke_action(APP, action, {})
    assert out["ok"] is False, out
    assert out["code"] == ADAPTER_NOT_BOUND, out
    assert out["bound"] is False, out


def test_rebinding_makes_it_usable_again(clean_store):
    """La controprova: senza questa, «rifiuta sempre» passerebbe il test sopra."""
    created = create_adapter(APP, hwnd=1001)
    action = created.actions[0].name
    _restart()

    assert rebind_adapter(APP, 1001) is not None
    assert get_adapter(APP).bound is True

    out = invoke_action(APP, action, {})
    assert out.get("code") != ADAPTER_NOT_BOUND, out


def test_rebinding_an_adapter_that_does_not_exist_returns_none(clean_store):
    assert rebind_adapter("nessun-adapter-con-questo-nome", 1001) is None


def test_list_adapters_says_which_ones_are_usable(clean_store):
    create_adapter(APP, hwnd=1001)
    assert list_adapters()[0]["bound"] is True

    _restart()
    row = list_adapters()[0]
    assert row["bound"] is False, row
    assert row["hwnd"] is None, row


# ---------------------------------------------------------------------------
# Fail-closed: versione e corruzione
# ---------------------------------------------------------------------------
def test_a_manifest_from_a_future_version_is_not_interpreted(clean_store):
    """Leggere un formato futuro con le regole di quello vecchio da' azioni sbagliate."""
    create_adapter(APP, hwnd=1001)
    path = next(clean_store.glob("*.json"))
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["manifest_version"] = store.MANIFEST_VERSION + 1
    path.write_text(json.dumps(manifest), encoding="utf-8")

    report = _restart()

    assert get_adapter(APP) is None, "un manifest di versione ignota e' stato caricato"
    assert report["restored"] == [], report
    assert report["skipped"], "lo scarto non e' stato riportato"
    assert report["skipped"][0]["code"] == store.MANIFEST_VERSION_UNKNOWN, report


def test_a_corrupt_manifest_is_skipped_and_reported(clean_store):
    """Saltato, ma non in silenzio: un adapter che sparisce senza motivo e'
    un adapter che il chiamante crede di avere."""
    create_adapter(APP, hwnd=1001)
    next(clean_store.glob("*.json")).write_text("{non e' json", encoding="utf-8")

    report = _restart()

    assert get_adapter(APP) is None
    assert report["skipped"][0]["code"] == store.MANIFEST_UNREADABLE, report
    assert report["skipped"][0]["reason"], "lo scarto non dice perche'"


@pytest.mark.parametrize(
    "mutation",
    [
        {"app_id": ""},
        {"app_id": None},
        {"actions": "non una lista"},
        {"actions": None},
    ],
)
def test_a_malformed_manifest_is_skipped_and_reported(clean_store, mutation):
    create_adapter(APP, hwnd=1001)
    path = next(clean_store.glob("*.json"))
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(mutation)
    path.write_text(json.dumps(manifest), encoding="utf-8")

    report = _restart()

    assert get_adapter(APP) is None, mutation
    assert report["skipped"][0]["code"] == store.MANIFEST_MALFORMED, report


def test_a_manifest_that_is_not_an_object_is_skipped(clean_store):
    create_adapter(APP, hwnd=1001)
    next(clean_store.glob("*.json")).write_text("[1, 2, 3]", encoding="utf-8")

    report = _restart()
    assert get_adapter(APP) is None
    assert report["skipped"][0]["code"] == store.MANIFEST_MALFORMED, report


def test_an_empty_store_is_not_an_error(clean_store):
    assert load_persisted_adapters() == {"restored": [], "skipped": []}


# ---------------------------------------------------------------------------
# Chi e' vivo vale piu' della sua fotografia
# ---------------------------------------------------------------------------
def test_a_live_adapter_is_not_replaced_by_the_one_on_disk(clean_store):
    """Ricaricare non deve sganciare un adapter che sta funzionando."""
    create_adapter(APP, hwnd=1001)
    report = load_persisted_adapters()  # senza riavvio: e' ancora in memoria

    assert report["restored"] == [], report
    assert get_adapter(APP).bound is True, "l'adapter vivo e' stato sganciato"


def test_the_runtime_restores_adapters_when_it_starts(clean_store):
    """Il reload deve avvenire all'AVVIO, non perche' un test lo chiama a mano.

    Senza questo, `load_persisted_adapters` sarebbe una funzione corretta che
    nessuno invoca, e il manifest un file che nessuno rilegge: la persistenza
    sarebbe vera nei test e falsa nel prodotto.
    """
    from fastapi.testclient import TestClient

    from windows_os_api.core.runtime.app import create_app

    created = create_adapter(APP, hwnd=1001)
    reset_adapters()  # il runtime riparte: la memoria e' vuota
    assert get_adapter(APP) is None, "premessa: la memoria deve essere vuota"

    with TestClient(create_app()):  # entrare nel context esegue il lifespan
        restored = get_adapter(APP)
        assert restored is not None, "l'avvio del runtime non ha ripristinato l'adapter"
        assert [a.name for a in restored.actions] == [a.name for a in created.actions]
        assert restored.bound is False, "ripristinato gia' agganciato: hwnd indovinato"


def test_the_store_path_follows_the_environment(tmp_path, monkeypatch):
    """Se non seguisse l'ambiente a ogni chiamata, la suite scriverebbe nel
    profilo di chi la lancia."""
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(tmp_path / "altrove"))
    assert store.store_dir() == tmp_path / "altrove"
    monkeypatch.delenv("WINOS_ADAPTER_STORE")
    assert store.store_dir().name == "adapters"


@pytest.mark.parametrize(
    "app_id",
    ["../../fuori", "..", ".", "/etc/passwd", r"..\..\fuori", "a/b/c", "~/altrove"],
)
def test_an_app_id_cannot_write_outside_the_store(clean_store, app_id):
    """Il nome del file non si costruisce con la fiducia.

    L'invariante e' il CONTENIMENTO — dove finisce il file — non l'estetica del
    nome: `.._.._fuori.json` contiene dei punti e sta esattamente dove deve.
    Confronto sui percorsi risolti, perche' e' quello che decide dove il disco
    scrive davvero.
    """
    path = store._manifest_path(app_id).resolve()
    root = clean_store.resolve()
    assert path.parent == root, (app_id, path)
    assert str(path).startswith(str(root)), (app_id, path)
