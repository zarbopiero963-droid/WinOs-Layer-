"""Il contratto `supported` attraverso HTTP, sulle route vere.

I test unitari coprono i cinque casi in isolamento. Questi verificano che
l'inviluppo arrivi davvero al chiamante — cioe' che le route non lo re-incartino
e non lo perdano — e che la compatibilita' additiva promessa all'owner sia vera
e non solo dichiarata.
"""
from __future__ import annotations

import pytest

READ_ONLY = [
    ("/v1/services", "services"),
    ("/v1/devices", "devices"),
    ("/v1/printers", "printers"),
    ("/v1/audio/devices", "devices"),
]


@pytest.mark.parametrize("path,key", READ_ONLY)
def test_every_read_only_endpoint_declares_whether_it_looked(client, auth_headers, path, key):
    """`supported` c'e' sempre: e' la domanda a cui prima nessuna risposta rispondeva."""
    body = client.get(path, headers=auth_headers).json()
    assert "supported" in body, f"{path} non dichiara se ha guardato: {body}"
    assert isinstance(body["supported"], bool), body


@pytest.mark.parametrize("path,key", READ_ONLY)
def test_the_old_key_is_still_there_and_still_a_list(client, auth_headers, path, key):
    """Compatibilita' additiva: nessun client esistente si accorge del cambiamento.

    Era il vincolo esplicito dell'owner. Se un giorno la chiave diventasse
    `items`, questo test lo direbbe prima degli utenti.
    """
    body = client.get(path, headers=auth_headers).json()
    assert key in body, f"{path} ha perso la chiave storica {key!r}: {body}"
    assert isinstance(body[key], list), body


@pytest.mark.parametrize("path,key", READ_ONLY)
def test_a_successful_read_carries_no_error_code(client, auth_headers, path, key):
    """Sul backend fake tutto e' supportato: nessuna risposta deve portare un codice.

    Se ne comparisse uno, «supportata con lista vuota» tornerebbe
    indistinguibile da un fallimento — il difetto che il contratto rimuove.
    """
    body = client.get(path, headers=auth_headers).json()
    assert body["supported"] is True, body
    assert "error_code" not in body, body


def test_audio_volume_answers_at_all(client, auth_headers):
    """Regressione: questa route rispondeva 500.

    `FakeBackend.audio_volume()` leggeva `self._muted`, mai inizializzato:
    `AttributeError` a processo fresco, quindi 500, finche' qualcuno non
    chiamava prima `PUT /v1/audio/mute`. Nessun test la interrogava, ed e'
    sopravvissuto cosi'.
    """
    r = client.get("/v1/audio/volume", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["supported"] is True, body
    assert body["volume"] is not None, body
    assert body["muted"] is False, body


def test_audio_volume_still_works_after_a_mute_round_trip(client, auth_headers):
    """E il valore riflette davvero cio' che e' stato impostato."""
    client.put("/v1/audio/mute", headers=auth_headers, json={"muted": True})
    body = client.get("/v1/audio/volume", headers=auth_headers).json()
    assert body["muted"] is True, body


def test_capabilities_lists_the_four_read_only_families(client, auth_headers):
    """`/v1/capabilities` deve nominare cio' che le route sanno riportare.

    Senza `devices` e `printers` nella tabella, un client che consultasse le
    capability prima di chiamare non troverebbe nulla su due dei quattro
    endpoint — e il contratto `supported` sulla singola risposta esiste proprio
    perche' quella seconda chiamata non debba essere obbligatoria.
    """
    flags = client.get("/v1/capabilities", headers=auth_headers).json()["feature_flags"]
    for family in ("services", "audio", "devices", "printers"):
        assert family in flags, f"{family} assente da feature_flags: {sorted(flags)}"
