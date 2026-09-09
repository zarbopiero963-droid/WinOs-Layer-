"""La denylist di lettura contro il registro VERO (decisione owner D6).

I test in `tests/security/` provano il gate contro un backend finto: dimostrano
che la regola scatta, non che le chiavi che nomina esistano davvero. Queste sono
chiavi reali di qualunque Windows — `HKLM\\SYSTEM\\CurrentControlSet\\Control\\Lsa`,
`...\\CurrentVersion\\Winlogon`, `HKU` — e il runner GitHub gira **elevato**,
quindi qui il rifiuto non puo' essere scambiato per una mancanza di permessi:
la macchina i permessi ce li ha, e la lettura viene rifiutata prima di provarci.

La prova che conta e' l'ultima: sullo stesso Windows, una chiave fuori dalle aree
vietate si legge ancora e restituisce un valore vero. Un gate che rifiuta tutto
sarebbe passato ugualmente su tutti gli altri test di questo file.
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.windows

if sys.platform != "win32":
    pytest.skip("requires real Windows", allow_module_level=True)

from windows_os_api.backends.windows import WindowsBackend  # noqa: E402
from windows_os_api.os.registry import service as reg  # noqa: E402
from windows_os_api.os.registry.allowlist import (  # noqa: E402
    REGISTRY_READ_FORBIDDEN,
    REGISTRY_VALUE_FORBIDDEN,
)


@pytest.fixture
def backend(tmp_path, monkeypatch):
    b = WindowsBackend(str(tmp_path))
    monkeypatch.setattr(
        "windows_os_api.os.registry.service.get_backend", lambda: b
    )
    return b


@pytest.mark.parametrize(
    "path",
    [
        r"HKLM\SAM",
        r"HKLM\SECURITY",
        r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa",
        r"HKLM\SYSTEM\ControlSet001\Control\Lsa",
        r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
        r"HKU\.DEFAULT\Software",
    ],
)
def test_a_real_sensitive_key_is_refused_on_a_real_windows(backend, path):
    """Chiavi che esistono davvero, su una macchina che avrebbe i privilegi.

    La fixture `backend` mette il `WindowsBackend` VERO dietro il service: senza
    di lei il test girerebbe contro il backend finto forzato da
    `tests/conftest.py`, e non direbbe niente sul registro di Windows.
    """
    result = reg.read(path)
    assert result["ok"] is False, result
    assert result["denied"] is True, result
    assert result["code"] == REGISTRY_READ_FORBIDDEN, result


def test_the_autologon_password_is_not_readable_by_name(backend):
    """Il valore per cui esiste questa patch.

    `DefaultPassword` sotto `Winlogon` e' in chiaro quando l'autologon e'
    attivo. Qui e' rifiutato due volte — l'area E il nome — e nessuna delle due
    dipende da come e' configurato il runner.
    """
    result = reg.read(
        r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
        "DefaultPassword",
    )
    assert result["ok"] is False, result
    assert result["code"] in (REGISTRY_READ_FORBIDDEN, REGISTRY_VALUE_FORBIDDEN), result


def test_the_windows_backend_is_never_asked_for_a_forbidden_key(backend, monkeypatch):
    """Il rifiuto arriva prima di `winreg.OpenKey`, non dopo un tentativo fallito.

    Su un runner elevato `HKLM\\SYSTEM\\...\\Lsa` si aprirebbe: se il gate fosse
    a valle, la chiamata riuscirebbe e il rifiuto sarebbe una finzione.
    """
    reached: list = []
    original = backend.registry_read

    def spy(path, name=None):
        reached.append((path, name))
        return original(path, name)

    monkeypatch.setattr(backend, "registry_read", spy)

    reg.read(r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa")
    assert reached == [], f"la chiave vietata ha raggiunto winreg: {reached}"

    # La premessa: lo spy funziona, e una lettura consentita lo attraversa.
    reg.read(r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName")
    assert reached, "lo spy non intercetta nulla: il test precedente non prova niente"


def test_an_allowed_key_still_returns_a_real_value(backend):
    """La controprova. Senza questa, un gate che nega tutto passerebbe.

    `ProductName` sotto `CurrentVersion` esiste su ogni Windows e non e' un
    segreto: e' la stringa che dice quale Windows e'.
    """
    result = reg.read(
        r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName"
    )
    assert result["ok"] is True, result
    assert isinstance(result["value"], str) and result["value"], result
    assert "Windows" in result["value"], result


def test_enumerating_an_allowed_key_withholds_the_product_id(backend):
    """`CurrentVersion` puo' contenere `DigitalProductId` accanto a cose innocue.

    E' il caso reale dell'aggiramento per enumerazione: la chiave e' consentita,
    e chiederla intera restituirebbe anche il codice prodotto. Non si asserisce
    che il runner ce l'abbia — si asserisce che, se c'e', non esce.
    """
    result = reg.read(r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion")
    assert result["ok"] is True, result
    for value_name in result["values"]:
        assert "PRODUCTID" not in value_name.upper(), result["values"].keys()
    # E cio' che resta e' una lettura vera, non un dizionario svuotato.
    assert result["values"], result
    assert any(k.lower() == "productname" for k in result["values"]), result["values"].keys()
