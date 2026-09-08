"""I cinque casi del contratto `supported` (decisione owner D3-A, issue #6).

Il difetto, misurato: `GET /v1/printers` rispondeva `{"printers": []}` in quattro
situazioni diverse e il chiamante non poteva distinguerle. Una sola di quelle
quattro e' la risposta che credeva di leggere.

I cinque casi richiesti dall'owner, e cosa li separa:

    1. supportata + risultati        supported: true,  lista piena
    2. supportata + lista vuota      supported: true,  lista vuota, NESSUN error_code
    3. non supportata                supported: false, CAPABILITY_NOT_SUPPORTED
    4. discovery fallita             supported: true,  DISCOVERY_FAILED
    5. capability non disponibile    supported: false, CAPABILITY_UNAVAILABLE

Il 3 e il 5 sembrano lo stesso caso e non lo sono, ed e' la distinzione che
rende il campo utile: «questo backend non implementa l'audio» (Windows senza
pycaw, decisione D4-B) non cambia installando niente; «lpstat non c'e' su questa
macchina» si risolve installando CUPS. Un unico codice per entrambi direbbe al
chiamante di arrendersi anche quando basta un pacchetto.
"""
from __future__ import annotations

import pytest

from windows_os_api.os.capability import (
    CAPABILITY_NOT_SUPPORTED,
    CAPABILITY_UNAVAILABLE,
    DISCOVERY_FAILED,
    DiscoveryFailed,
    discover,
    unsupported,
)


class _Backend:
    """Backend minimo: dichiara i flag e cosa non implementa affatto."""

    def __init__(self, flags=None, not_implemented=(), name="test"):
        self.name = name
        self._flags = flags or {}
        self.NOT_IMPLEMENTED = frozenset(not_implemented)

    def capability_flags(self):
        return dict(self._flags)


# ---------------------------------------------------------------------------
# I cinque casi
# ---------------------------------------------------------------------------
def test_case_1_supported_with_results():
    out = discover(_Backend({"printers": True}), "printers", "printers",
                   lambda: [{"name": "HP"}])
    assert out["supported"] is True
    assert out["printers"] == [{"name": "HP"}]
    assert "error_code" not in out, "un successo non porta un codice d'errore"


def test_case_2_supported_but_nothing_found():
    """Il caso che prima era indistinguibile da tutti gli altri.

    Qui `[]` significa davvero «non ce ne sono», e l'assenza di `error_code` e'
    cio' che lo dice.
    """
    out = discover(_Backend({"printers": True}), "printers", "printers", lambda: [])
    assert out["supported"] is True
    assert out["printers"] == []
    assert "error_code" not in out, (
        "una lista vuota legittima non deve portare un codice d'errore: "
        "sarebbe di nuovo indistinguibile da un fallimento"
    )


def test_case_3_not_implemented_by_this_backend():
    """Windows + audio: nessuna installazione lo abilita (D4-B)."""
    backend = _Backend({"audio": False}, not_implemented={"audio"}, name="windows")
    out = discover(backend, "audio", "devices", lambda: [])
    assert out["supported"] is False
    assert out["error_code"] == CAPABILITY_NOT_SUPPORTED
    assert out["devices"] == []
    assert "non e' una questione di configurazione" in out["reason"]


def test_case_4_discovery_failed():
    """Supportata, tentata, fallita — e non «non ce ne sono».

    Rispondere `[]` a un timeout di `lpstat` affermerebbe che questa macchina
    non ha stampanti. Non lo sappiamo, ed e' un'altra cosa.
    """
    def boom():
        raise DiscoveryFailed("lpstat -a fallita: timeout")

    out = discover(_Backend({"printers": True}), "printers", "printers", boom)
    assert out["supported"] is True, "il fallimento non nega la capability"
    assert out["error_code"] == DISCOVERY_FAILED
    assert out["printers"] == []
    assert "timeout" in out["reason"], "il motivo reale deve arrivare al chiamante"


def test_case_5_implemented_but_unavailable_here():
    """Linux + printers senza `lpstat`: si risolve installando CUPS."""
    backend = _Backend({"printers": False}, not_implemented=(), name="linux")
    out = discover(backend, "printers", "printers", lambda: [])
    assert out["supported"] is False
    assert out["error_code"] == CAPABILITY_UNAVAILABLE
    assert "manca cio' che serve" in out["reason"]


def test_cases_3_and_5_are_not_the_same_answer():
    """La distinzione che rende il campo utile, asserita esplicitamente.

    Se un giorno qualcuno unificasse i due codici, questo test lo direbbe.
    """
    never = discover(_Backend({"audio": False}, {"audio"}, "windows"), "audio", "devices", lambda: [])
    missing = discover(_Backend({"audio": False}, (), "linux"), "audio", "devices", lambda: [])
    assert never["error_code"] != missing["error_code"], (
        "«non implementato» e «non disponibile qui» portano a due azioni diverse "
        "e non possono condividere un codice"
    )


# ---------------------------------------------------------------------------
# Proprieta' del contratto
# ---------------------------------------------------------------------------
def test_the_data_key_is_always_present_whatever_happened():
    """Additivo: chi legge solo `printers` non si accorge di niente.

    Era il vincolo esplicito dell'owner — nessun client esistente deve rompersi.
    """
    scenarios = [
        discover(_Backend({"printers": True}), "printers", "printers", lambda: [{"n": 1}]),
        discover(_Backend({"printers": True}), "printers", "printers", lambda: []),
        discover(_Backend({"printers": False}), "printers", "printers", lambda: []),
    ]
    for out in scenarios:
        assert "printers" in out, out
        assert isinstance(out["printers"], list), out


def test_an_unknown_capability_is_treated_as_supported():
    """L'assenza dalla tabella non e' un «no».

    Significa «questo backend non tiene quel flag». Dedurne `false` sarebbe la
    stessa invenzione che il contratto esiste per togliere — e trasformerebbe
    ogni backend senza tabella in uno che non supporta niente.
    """
    out = discover(_Backend({}), "printers", "printers", lambda: [{"n": 1}])
    assert out["supported"] is True
    assert out["printers"] == [{"n": 1}]


def test_a_backend_without_a_flags_table_still_works():
    class Bare:
        name = "bare"

    out = discover(Bare(), "printers", "printers", lambda: [])
    assert out["supported"] is True


def test_a_flags_table_that_raises_does_not_take_the_call_down():
    class Broken:
        name = "broken"

        def capability_flags(self):
            raise RuntimeError("tabella rotta")

    out = discover(Broken(), "printers", "printers", lambda: [{"n": 1}])
    assert out["supported"] is True, "un difetto nella tabella non deve negare i dati"


def test_only_discovery_failed_is_caught_not_every_error():
    """Un bug nel backend deve restare visibile, non diventare `[]`.

    `DiscoveryFailed` e' il modo in cui un backend dice «ho provato e non ce
    l'ho fatta». Qualunque altra eccezione e' un difetto del codice, e
    inghiottirla la trasformerebbe in una lista vuota — cioe' ricreerebbe
    esattamente il problema che questo contratto risolve.
    """
    def bug():
        raise ValueError("questo e' un bug, non un fallimento di discovery")

    with pytest.raises(ValueError):
        discover(_Backend({"printers": True}), "printers", "printers", bug)


# ---------------------------------------------------------------------------
# Il valore singolo (volume), che non e' una lista
# ---------------------------------------------------------------------------
def test_a_single_value_says_unknown_not_null():
    """`{"volume": null}` diceva «il volume e' zero/nullo». Non lo sappiamo."""
    backend = _Backend({"audio": False}, not_implemented={"audio"}, name="windows")
    out = unsupported(backend, "audio", "volume")
    assert out["supported"] is False
    assert out["volume"] is None
    assert out["error_code"] == CAPABILITY_NOT_SUPPORTED
    assert out["reason"]
