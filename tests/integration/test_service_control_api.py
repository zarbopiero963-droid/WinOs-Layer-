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
