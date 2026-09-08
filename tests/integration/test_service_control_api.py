"""`POST /v1/services/{name}` attraverso HTTP: default-deny e 403.

Due proprieta' che i test unitari non coprono:

* il rifiuto arriva al chiamante come **403**, non come `200` con un campo
  dentro — un rifiuto che risponde `200` e' un successo per chiunque guardi lo
  status code, ed e' la stessa forma che gia' usa il rifiuto della policy
  sandbox in `apps.py`;
* il permesso `SERVICE_CONTROL` **non** basta: e' l'autorizzazione a chiedere,
  non a ottenere. L'allowlist decide dopo.
"""
from __future__ import annotations

import pytest

from windows_os_api.os.services.allowlist import ENV_VAR


@pytest.fixture(autouse=True)
def clean_allowlist(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)


def test_the_default_refuses_every_service_with_403(client, auth_headers):
    """Default-deny visto da fuori: nessuna configurazione, nessun controllo."""
    for unit in ("nginx", "ssh", "firewalld", "systemd-journald"):
        r = client.post(f"/v1/services/{unit}", headers=auth_headers,
                        json={"action": "stop"})
        assert r.status_code == 403, f"{unit} non rifiutato: {r.status_code} {r.text}"


def test_having_the_permission_is_not_having_the_authorisation(client, auth_headers):
    """`SERVICE_CONTROL` autorizza a CHIEDERE, non a ottenere.

    Se il permesso bastasse, l'allowlist sarebbe un suggerimento. La chiave usata
    da `auth_headers` ha il permesso e viene comunque rifiutata.
    """
    r = client.post("/v1/services/ssh", headers=auth_headers, json={"action": "stop"})
    assert r.status_code == 403, r.text
    assert "WINOS_SERVICE_ALLOWLIST" in r.text, r.text


def test_the_admin_key_is_refused_exactly_like_any_other(client, admin_headers):
    """Decisione owner: ADMIN non e' autorizzazione implicita globale."""
    r = client.post("/v1/services/ssh", headers=admin_headers, json={"action": "stop"})
    assert r.status_code == 403, r.text


def test_a_service_in_the_allowlist_is_not_refused_by_the_gate(client, auth_headers, monkeypatch):
    """Il gate autorizza, non blocca soltanto.

    Sul backend fake l'esito dell'azione e' quello che e'; cio' che conta qui e'
    che NON sia piu' un 403 dell'allowlist — cioe' che la richiesta sia passata.
    Senza questo test, un `raise HTTPException(403)` incondizionato passerebbe
    tutti gli altri.
    """
    monkeypatch.setenv(ENV_VAR, "nginx,spooler")
    r = client.post("/v1/services/spooler", headers=auth_headers, json={"action": "status"})
    assert r.status_code != 403, r.text


def test_the_refusal_names_the_variable_to_configure(client, auth_headers):
    """Un rifiuto su cui l'operatore non puo' agire e' meta' rifiuto."""
    r = client.post("/v1/services/nginx", headers=auth_headers, json={"action": "restart"})
    assert r.status_code == 403
    detail = r.json().get("detail", "")
    assert ENV_VAR in detail, detail
    assert "default-deny" in detail, detail


# ---------------------------------------------------------------------------
# `PUT /v1/registry` — allowlist di prefissi (decisione owner D2-B)
# ---------------------------------------------------------------------------
from windows_os_api.os.registry.allowlist import ENV_VAR as REGISTRY_ENV  # noqa: E402


@pytest.fixture(autouse=True)
def clean_registry_allowlist(monkeypatch):
    monkeypatch.delenv(REGISTRY_ENV, raising=False)


def test_a_write_under_hkcu_software_is_allowed(client, auth_headers):
    """Il default sicuro funziona: le impostazioni dell'app si scrivono."""
    r = client.put("/v1/registry", headers=auth_headers,
                   json={"path": r"HKCU\Software\WinOsTest", "name": "k", "value": "v"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True, r.text


def test_writes_outside_the_allowlist_are_403(client, auth_headers):
    for path in (r"HKLM\SOFTWARE\Microsoft", r"HKCU\Environment", r"HKCU\SoftwareAltro\X"):
        r = client.put("/v1/registry", headers=auth_headers,
                       json={"path": path, "name": "k", "value": "v"})
        assert r.status_code == 403, f"{path}: {r.status_code} {r.text}"


def test_the_critical_areas_are_403_however_they_are_written(client, auth_headers):
    """Lo stesso posto scritto in quattro modi deve avere lo stesso esito."""
    for path in (
        r"HKLM\SYSTEM\CurrentControlSet",
        r"HKEY_LOCAL_MACHINE\SYSTEM\Foo",
        r"HKLM\SECURITY\Policy",
        r"HKLM\SAM\SAM",
    ):
        r = client.put("/v1/registry", headers=auth_headers,
                       json={"path": path, "name": "k", "value": "v"})
        assert r.status_code == 403, f"{path}: {r.status_code} {r.text}"


def test_the_admin_key_cannot_write_outside_the_allowlist(client, admin_headers):
    """Nessun write arbitrario con solo ADMIN, per decisione owner."""
    r = client.put("/v1/registry", headers=admin_headers,
                   json={"path": r"HKLM\SYSTEM\Foo", "name": "k", "value": "v"})
    assert r.status_code == 403, r.text


def test_the_allowlist_extends_the_reachable_paths(client, auth_headers, monkeypatch):
    r = client.put("/v1/registry", headers=auth_headers,
                   json={"path": r"HKCU\Tools\App", "name": "k", "value": "v"})
    assert r.status_code == 403, r.text

    monkeypatch.setenv(REGISTRY_ENV, "HKCU\\Tools\\")
    r = client.put("/v1/registry", headers=auth_headers,
                   json={"path": r"HKCU\Tools\App", "name": "k", "value": "v"})
    assert r.status_code == 200, r.text


def test_a_written_value_can_be_read_back_at_the_same_path(client, auth_headers):
    """Il gate non deve spostare la scrittura.

    Se `check()` restituisse la chiave di confronto (maiuscola), la scrittura
    finirebbe su `HKCU\\SOFTWARE\\...` e questa rilettura non troverebbe niente:
    gli store di Fake e Linux sono dizionari, e per un dizionario `Software` e
    `SOFTWARE` sono due chiavi.
    """
    path = r"HKCU\Software\RoundTrip"
    client.put("/v1/registry", headers=auth_headers,
               json={"path": path, "name": "Setting", "value": "on"})
    r = client.get("/v1/registry", headers=auth_headers, params={"path": path, "name": "Setting"})
    assert r.status_code == 200, r.text
    assert r.json().get("value") == "on", r.text


# ---------------------------------------------------------------------------
# 501 vs 403 — «non so farlo» e «non ti e' permesso» (decisione owner D5-B)
# ---------------------------------------------------------------------------
class _NoServiceControl:
    """Backend che dichiara di non implementare il controllo dei servizi."""

    name = "windows"
    NOT_IMPLEMENTED = frozenset({"service_control"})

    def capability_flags(self):
        return {"services": True, "service_control": False}

    def control_service(self, name, action, **kw):
        raise AssertionError("il backend non deve essere raggiunto")


def test_an_unsupported_capability_answers_501_not_403(client, auth_headers, monkeypatch):
    """Due domande diverse, due risposte diverse.

    Sul 403 il chiamante puo' chiedere un'autorizzazione; sul 501 non c'e'
    niente da chiedere. Un unico codice manderebbe l'operatore a configurare
    un'allowlist che non cambierebbe niente.
    """
    monkeypatch.setattr(
        "windows_os_api.os.services.service.get_backend", lambda: _NoServiceControl()
    )
    r = client.post("/v1/services/spooler", headers=auth_headers, json={"action": "stop"})
    assert r.status_code == 501, f"{r.status_code}: {r.text}"
    assert "non e' una questione di configurazione" in r.text, r.text


def test_the_501_does_not_change_when_the_service_is_allowlisted(
    client, auth_headers, monkeypatch
):
    """La prova che l'ordine e' giusto: autorizzarlo non lo fa funzionare."""
    monkeypatch.setattr(
        "windows_os_api.os.services.service.get_backend", lambda: _NoServiceControl()
    )
    monkeypatch.setenv(ENV_VAR, "spooler")
    r = client.post("/v1/services/spooler", headers=auth_headers, json={"action": "stop"})
    assert r.status_code == 501, f"{r.status_code}: {r.text}"


def test_a_supporting_backend_still_answers_403_for_the_allowlist(client, auth_headers):
    """Sul backend fake il controllo E' supportato: il rifiuto resta 403.

    Senza questo, un 501 incondizionato passerebbe i due test sopra.
    """
    r = client.post("/v1/services/ssh", headers=auth_headers, json={"action": "stop"})
    assert r.status_code == 403, f"{r.status_code}: {r.text}"
