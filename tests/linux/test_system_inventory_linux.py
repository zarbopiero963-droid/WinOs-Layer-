"""`list_devices` contro il vero `/sys/block` di questa macchina.

Il difetto corretto qui è piccolo e vecchio: ogni riga usciva con
`"status": "ok"` — un giudizio di salute che nulla aveva controllato. Una voce
in `/sys/block` sostiene una sola affermazione, che il dispositivo è presente, e
quella è ciò che la riga dice adesso. `media` viene letto da `removable`, un
file che il kernel mantiene davvero.

Perché conta, visto che è "solo una parola": è la stessa classe di difetto di
`ok: true` su una finestra inesistente (#24) e di `state: null` presentato come
`verified: true` (#18). Un campo che dice sempre la stessa cosa non è
un'informazione, è rumore che sembra un'informazione.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.linux

if sys.platform == "win32":
    pytest.skip("LinuxBackend inventory", allow_module_level=True)

from windows_os_api.backends.linux import LinuxBackend  # noqa: E402


@pytest.fixture
def backend(tmp_path):
    return LinuxBackend(sandbox_root=str(tmp_path / "sandbox"))


def test_the_reported_devices_are_the_ones_the_kernel_lists(backend):
    """Non un sottoinsieme scelto a mano: esattamente ciò che c'è in `/sys/block`.

    Se il runner non ha `/sys/block` (container minimale) la lista è vuota e il
    confronto resta vero — l'asserzione è sull'uguaglianza con la realtà, non
    sulla presenza di un disco.
    """
    sys_block = Path("/sys/block")
    expected = {d.name for d in sorted(sys_block.iterdir())[:50]} if sys_block.is_dir() else set()
    reported = {d["id"] for d in backend.list_devices()}
    assert reported == expected, f"riportati {reported}, presenti {expected}"


def test_status_is_no_longer_the_word_ok(backend):
    """La regressione diretta: `"ok"` era un verdetto mai misurato."""
    statuses = {d["status"] for d in backend.list_devices()}
    assert "ok" not in statuses, (
        "'status': 'ok' è tornato — è un giudizio di salute che nessuno verifica"
    )


def test_every_row_claims_only_presence(backend):
    for d in backend.list_devices():
        assert d["status"] == "present", d
        assert d["type"] == "block", d
        assert d["id"] == d["name"], d


def test_media_matches_the_removable_flag_the_kernel_exposes(backend):
    """`media` deve corrispondere al file `removable`, letto adesso.

    Confronto contro la fonte, non contro un valore atteso: così il test resta
    vero su qualunque macchina e fallisce solo se il codice smette di leggere
    ciò che dice di leggere.
    """
    for d in backend.list_devices():
        flag_file = Path("/sys/block") / d["id"] / "removable"
        if not flag_file.exists():
            assert d["media"] == "unknown", d
            continue
        try:
            raw = flag_file.read_text().strip()
        except OSError:
            continue
        expected = "removable" if raw == "1" else "fixed"
        assert d["media"] == expected, f"{d} contro removable={raw!r}"


def test_an_unreadable_removable_flag_becomes_unknown_not_fixed(backend, monkeypatch):
    """Un errore di lettura non deve diventare la risposta "fixed".

    Dedurre "fixed" da un fallimento sarebbe inventare: è esattamente il difetto
    che questa PR toglie da `list_services` su Windows, nella sua versione
    piccola.
    """
    sys_block = Path("/sys/block")
    if not sys_block.is_dir() or not any(sys_block.iterdir()):
        pytest.skip("nessun block device su questa macchina")

    real_read = Path.read_text

    def boom(self, *args, **kwargs):
        if self.name == "removable":
            raise OSError("simulated unreadable sysfs")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)
    devices = backend.list_devices()
    assert devices, "la lista non deve svuotarsi per un errore di lettura"
    assert {d["media"] for d in devices} == {"unknown"}, devices
    # E la presenza resta affermabile: il device c'è, è il flag a non leggersi.
    assert {d["status"] for d in devices} == {"present"}, devices
