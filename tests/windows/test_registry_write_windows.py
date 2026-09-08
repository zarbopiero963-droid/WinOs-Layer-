"""Scrittura reale nel registro di Windows, sotto `HKCU\\Software\\`.

`registry_write` era uno stub: restituiva `{"ok": False, "error": "registry
write requires elevation"}` **incondizionatamente** — non tentava mai, nemmeno
sotto `HKCU`, dove nessuna elevazione serve. Un errore sempre uguale non dice
niente sul perche', e un gate davanti a una porta che non si apre sarebbe teatro.

Questi test scrivono davvero, sotto una chiave di prova dentro il prefisso
autorizzato, e ripuliscono. Girano su `windows-latest`: `HKCU` e' scrivibile
dall'utente del runner senza privilegi.
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.windows

if sys.platform != "win32":
    pytest.skip("requires real Windows", allow_module_level=True)

from windows_os_api.backends.windows import WindowsBackend  # noqa: E402
from windows_os_api.os.registry import service as reg  # noqa: E402
from windows_os_api.os.registry.allowlist import ENV_VAR  # noqa: E402

TEST_KEY = r"HKCU\Software\WinOsApiTest"


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    b = WindowsBackend(str(tmp_path))
    yield b
    # Pulizia: la chiave di prova non deve sopravvivere al test.
    try:
        import winreg  # type: ignore

        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\WinOsApiTest")
    except OSError:
        pass


def test_a_string_value_is_written_and_read_back(backend):
    """`ok: true` significa «c'e' scritto quello», non «non ha sollevato».

    Il valore viene riletto dentro `registry_write` prima di rispondere: e' la
    stessa lezione di #18 e #24 — la richiesta non e' il risultato.
    """
    out = backend.registry_write(TEST_KEY, "Greeting", "ciao")
    assert out["ok"] is True, out
    assert out["verified"] is True, out
    assert out["value"] == "ciao", out

    back = backend.registry_read(TEST_KEY, "Greeting")
    assert back["ok"] is True, back
    assert back["value"] == "ciao", back


def test_an_integer_keeps_its_type(backend):
    """Un intero scritto come stringa tornerebbe indietro come stringa.

    Chi lo rilegge troverebbe un tipo diverso da quello che ha scritto, e se ne
    accorgerebbe solo confrontando.
    """
    out = backend.registry_write(TEST_KEY, "Answer", 42)
    assert out["ok"] is True, out
    assert out["value"] == 42, out
    assert isinstance(out["value"], int), out

    back = backend.registry_read(TEST_KEY, "Answer")
    assert back["value"] == 42, back


def test_the_stub_message_is_gone(backend):
    """La regressione diretta: nessuna risposta puo' piu' essere quella fissa."""
    out = backend.registry_write(TEST_KEY, "Probe", "x")
    assert out.get("error") != "registry write requires elevation", out


def test_an_unknown_hive_is_reported_not_written(backend):
    out = backend.registry_write(r"HKXX\Software\Foo", "k", "v")
    assert out["ok"] is False
    assert "unknown hive" in out["error"], out


def test_the_gate_refuses_before_the_registry_is_touched(backend, monkeypatch):
    """Il percorso vietato non arriva a `winreg`.

    Attraverso `os.registry.service.write`, che e' il punto che ogni superficie
    attraversa: il backend viene sostituito con uno che solleva se toccato.
    """
    touched = []

    class _Explodes:
        name = "explodes"

        def registry_write(self, path, name, value):
            touched.append(path)
            raise AssertionError(f"il registro e' stato toccato: {path}")

    monkeypatch.setattr(
        "windows_os_api.os.registry.service.get_backend", lambda: _Explodes()
    )
    out = reg.write(r"HKLM\SYSTEM\CurrentControlSet", "k", "v")
    assert out["denied"] is True, out
    assert touched == [], touched


def test_an_authorised_write_goes_through_the_service_layer(backend, monkeypatch):
    """Il giro completo: gate + backend reale, sotto il prefisso di default."""
    monkeypatch.setattr(
        "windows_os_api.os.registry.service.get_backend", lambda: backend
    )
    out = reg.write(TEST_KEY, "ViaService", "ok")
    assert out["ok"] is True, out
    assert backend.registry_read(TEST_KEY, "ViaService")["value"] == "ok"
