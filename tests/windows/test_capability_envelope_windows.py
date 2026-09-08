"""Il contratto `supported` contro il vero WindowsBackend.

Su Windows i due rami opposti convivono sulla stessa macchina, ed e' il modo
piu' onesto di verificare la distinzione:

    servizi/stampanti/volumi  ->  supported: true   (pywin32 c'e', si enumera)
    audio                     ->  supported: false  (D4-B: niente pycaw)

Prima l'audio rispondeva `200` con lista vuota, cioe' «questa macchina non ha
dispositivi audio» — falso su qualunque PC.
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.windows

if sys.platform != "win32":
    pytest.skip("requires real Windows", allow_module_level=True)

from windows_os_api.backends.windows import WindowsBackend  # noqa: E402
from windows_os_api.os.capability import (  # noqa: E402
    CAPABILITY_NOT_SUPPORTED,
    CAPABILITY_UNAVAILABLE,
    DISCOVERY_FAILED,
    DiscoveryFailed,
    discover,
)


@pytest.fixture
def backend(tmp_path):
    return WindowsBackend(str(tmp_path))


def test_audio_is_declared_unimplemented_not_empty(backend):
    """D4-B, verificato sul backend vero.

    `CAPABILITY_NOT_SUPPORTED` e non `CAPABILITY_UNAVAILABLE`: installare
    qualcosa su questa macchina non abilita l'audio, servirebbe `pycaw` nel
    progetto. La differenza dice al chiamante se ha senso provare.
    """
    out = discover(backend, "audio", "devices", backend.audio_devices)
    assert out["supported"] is False, out
    assert out["error_code"] == CAPABILITY_NOT_SUPPORTED, out
    assert out["error_code"] != CAPABILITY_UNAVAILABLE
    assert out["devices"] == []
    assert "non e' una questione di configurazione" in out["reason"]


def test_services_are_supported_and_enumerated(backend):
    """Il caso 1: supportata, con risultati, e nessun codice d'errore."""
    out = discover(backend, "services", "services", backend.list_services)
    assert out["supported"] is True, out
    assert "error_code" not in out, out
    assert len(out["services"]) > 10, out


def test_printers_are_supported_even_when_there_are_none(backend):
    """Il caso 2, quello che prima era indistinguibile da tutto il resto.

    Il runner puo' non avere stampanti: `supported: true` con lista vuota
    significa «ho guardato e non ce ne sono», e l'assenza di `error_code` e'
    cio' che lo dice.
    """
    out = discover(backend, "printers", "printers", backend.list_printers)
    assert out["supported"] is True, out
    assert "error_code" not in out, out
    assert isinstance(out["printers"], list)


def test_volumes_are_supported_and_include_the_system_drive(backend):
    out = discover(backend, "devices", "devices", backend.list_devices)
    assert out["supported"] is True, out
    assert any(d["id"] == "C:" for d in out["devices"]), out


def test_a_failing_enumeration_is_a_failure_not_an_empty_list(backend, monkeypatch):
    """Il caso 4 sul backend vero: il SCM che non risponde non significa «zero servizi».

    Prima il backend inghiottiva l'eccezione e restituiva `[]`. Un errore del
    Service Control Manager sarebbe diventato l'affermazione che questa
    macchina non ha servizi — che su Windows e' impossibile.
    """
    class _Broken:
        SC_MANAGER_ENUMERATE_SERVICE = 4
        SERVICE_WIN32 = 48
        SERVICE_STATE_ALL = 3

        def OpenSCManager(self, *a, **k):
            raise OSError("accesso al SCM negato")

        def CloseServiceHandle(self, *a, **k):
            pass

    monkeypatch.setattr(backend, "_win32service", _Broken())

    with pytest.raises(DiscoveryFailed):
        backend.list_services()

    out = discover(backend, "services", "services", backend.list_services)
    assert out["supported"] is True, "il fallimento non nega la capability"
    assert out["error_code"] == DISCOVERY_FAILED, out
    assert out["services"] == []
    assert "SCM" in out["reason"] or "negato" in out["reason"], out


def test_the_capability_table_matches_what_pywin32_provides(backend):
    """I flag devono riflettere i moduli davvero importati, non una tabella fissa.

    E' il difetto tolto in `os/system/service.py`: una tabella scritta a mano
    diceva `services: False` mentre il backend enumerava i servizi.
    """
    flags = backend.capability_flags()
    assert flags["services"] is (backend._win32service is not None)
    assert flags["printers"] is (backend._win32print is not None)
    assert flags["devices"] is (backend._win32api is not None)
    assert flags["audio"] is False, "D4-B: nessun pycaw, quindi nessun audio"
