"""Il contratto `supported` contro il vero LinuxBackend di questa macchina.

Qui i cinque casi non sono simulati: `supported` viene da cio' che c'e'
davvero installato. Il runner CI ha `systemctl` ma non `lpstat`, quindi i due
rami opposti sono entrambi esercitati sulla stessa macchina — che e' il modo
piu' onesto di testare una distinzione fra «c'e'» e «non c'e'».
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.linux

if sys.platform == "win32":
    pytest.skip("LinuxBackend capability envelope", allow_module_level=True)

from windows_os_api.backends.linux import LinuxBackend  # noqa: E402
from windows_os_api.os.capability import (  # noqa: E402
    CAPABILITY_NOT_SUPPORTED,
    CAPABILITY_UNAVAILABLE,
    DISCOVERY_FAILED,
    DiscoveryFailed,
    discover,
)


@pytest.fixture
def backend(tmp_path):
    return LinuxBackend(sandbox_root=str(tmp_path / "sandbox"))


def test_the_flags_match_what_is_actually_installed(backend):
    """Non un valore atteso: il confronto e' contro la macchina.

    Cosi' il test resta vero su qualunque runner e fallisce solo se il codice
    smette di misurare cio' che dice di misurare.
    """
    flags = backend.capability_flags()
    assert flags["services"] == bool(shutil.which("systemctl"))
    assert flags["printers"] == bool(shutil.which("lpstat"))
    assert flags["devices"] == Path("/sys/block").is_dir()


def test_a_capability_whose_tool_is_missing_says_unavailable_not_unsupported(backend):
    """Il caso 5, su una capability che manca davvero qui.

    Se `lpstat` c'e' su questo runner il test si salta: asserirebbe su una
    situazione che non esiste, e un test che verifica il contrario di cio' che
    accade non prova niente.
    """
    if shutil.which("lpstat"):
        pytest.skip("lpstat presente: qui il caso 5 non si verifica")

    out = discover(backend, "printers", "printers", backend.list_printers)
    assert out["supported"] is False
    assert out["error_code"] == CAPABILITY_UNAVAILABLE, (
        "su Linux le stampanti SONO implementate: manca lo strumento, non la "
        "capability, e la differenza dice al chiamante se installare CUPS aiuta"
    )
    assert out["error_code"] != CAPABILITY_NOT_SUPPORTED


def test_a_capability_that_is_present_reports_supported(backend):
    """Il caso 1 o 2 — supportata, con o senza elementi, mai un error_code."""
    if not Path("/sys/block").is_dir():
        pytest.skip("nessun /sys/block su questa macchina")

    out = discover(backend, "devices", "devices", backend.list_devices)
    assert out["supported"] is True
    assert "error_code" not in out, out
    assert isinstance(out["devices"], list)


def test_a_failing_lpstat_is_reported_as_a_failure_not_as_no_printers(tmp_path, monkeypatch):
    """Il caso 4, contro il backend vero.

    `lpstat` presente ma in errore (CUPS giu', timeout) non dice nulla
    sull'esistenza delle stampanti. Prima il backend inghiottiva l'eccezione e
    rispondeva `[]`, cioe' affermava che non ce n'erano.

    Il backend si costruisce DOPO la patch, non prima: `_probe_capabilities`
    gira in `__init__`, quindi un backend gia' istanziato porterebbe i flag
    della macchina reale — dove `lpstat` non c'e' — e `discover` corto-
    circuiterebbe sul flag senza mai arrivare a `lpstat`. Ci sono cascato
    scrivendo questo test: il codice era giusto, la patch arrivava tardi.
    """
    real_which = shutil.which
    monkeypatch.setattr(
        shutil, "which",
        lambda name: "/usr/bin/lpstat" if name == "lpstat" else real_which(name),
    )

    def dead(*args, **kwargs):
        raise TimeoutError("CUPS non risponde")

    monkeypatch.setattr("windows_os_api.backends.linux.subprocess.run", dead)

    backend = LinuxBackend(sandbox_root=str(tmp_path / "sandbox"))
    assert backend.capability_flags()["printers"] is True, (
        "presupposto del test: il backend deve credere che lpstat ci sia"
    )

    with pytest.raises(DiscoveryFailed):
        backend.list_printers()

    out = discover(backend, "printers", "printers", backend.list_printers)
    assert out["supported"] is True, "il fallimento non nega la capability"
    assert out["error_code"] == DISCOVERY_FAILED
    assert out["printers"] == []
    assert "CUPS" in out["reason"], out


def test_a_missing_lpstat_is_not_a_discovery_failure(backend, monkeypatch):
    """Simmetrico del precedente: assenza dello strumento ≠ errore.

    `lpstat` che non c'e' e' una capability mancante, e la riporta il flag; se
    diventasse `DiscoveryFailed`, ogni macchina senza CUPS sembrerebbe rotta.
    """
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert backend.list_printers() == []
