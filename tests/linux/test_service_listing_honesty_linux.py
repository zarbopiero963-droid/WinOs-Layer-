"""`list_services` su Linux non inventa righe e non nasconde un bus morto.

Due difetti trovati aprendo `linux_services.py` per l'allowlist, entrambi della
stessa famiglia gia' chiusa in #27 e #28:

1. **Due servizi inventati.** Quando `systemctl` mancava restituiva un servizio
   chiamato `"systemctl"`; quando non c'erano unit, uno chiamato `"none"`.
   Nomi che nessun sistema ha, su cui il chiamante agisce e sbaglia — la stessa
   cosa del `WinOsApi` tolto in #27.

2. **Systemd irraggiungibile riportato come «nessun servizio».** In un container
   il binario `systemctl` c'e' ma il bus no: ogni comando fallisce, `stdout` e'
   vuoto, e il parser produceva `[]`. Il flag `services` diceva `true` (il
   binario esiste), quindi l'API rispondeva «supportata, zero servizi» — cioe'
   affermava un fatto che non aveva verificato. Questa macchina e' esattamente
   in quello stato, quindi il caso e' testabile qui per davvero.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.linux

if sys.platform == "win32":
    pytest.skip("systemd listing", allow_module_level=True)

from windows_os_api.backends import linux_services as lsvc  # noqa: E402
from windows_os_api.os.capability import DISCOVERY_FAILED, DiscoveryFailed, discover  # noqa: E402


def _systemd_is_usable() -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        r = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--no-pager", "--plain"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:  # noqa: BLE001
        return False
    return r.returncode == 0


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# ---------------------------------------------------------------------------
# Nessuna riga inventata
# ---------------------------------------------------------------------------
def test_a_missing_systemctl_yields_no_rows_not_a_fake_one(monkeypatch):
    """Restituiva `[{"name": "systemctl", "status": "unavailable"}]`.

    Non esiste un servizio chiamato «systemctl». Che il binario manchi lo
    riporta il flag `services`, che e' il posto giusto per dirlo.
    """
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert lsvc.list_services() == []


def test_no_units_yields_an_empty_list_not_a_service_called_none(monkeypatch):
    """Restituiva `[{"name": "none", "status": "empty"}]`.

    Zero unit e' gia' perfettamente dicibile con una lista vuota; una riga
    chiamata «none» e' un servizio che il chiamante puo' provare a fermare.
    """
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/systemctl")
    # returncode 0 con stdout vuoto: comando riuscito, nessuna unit.
    assert lsvc.list_services(run=lambda *a, **k: _Proc(stdout="", returncode=0)) == []


def test_the_invented_names_are_gone_from_the_module():
    """Guardiano strutturale: i due nomi non devono tornare come letterali."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(lsvc))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]
    assert "systemctl not found" not in literals
    assert "no units listed" not in literals


# ---------------------------------------------------------------------------
# Systemd irraggiungibile != nessun servizio
# ---------------------------------------------------------------------------
def test_an_unreachable_bus_is_a_discovery_failure(monkeypatch):
    """Il binario c'e', il bus no: e' un fallimento, non «zero servizi»."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/systemctl")

    def dead(*a, **k):
        return _Proc(stderr="Failed to connect to bus: No medium found", returncode=1)

    with pytest.raises(DiscoveryFailed) as exc:
        lsvc.list_services(run=dead)
    assert "bus" in str(exc.value), exc.value


def test_this_machine_reports_the_truth_about_its_own_systemd():
    """Contro il sistema reale, qualunque esso sia.

    In un container senza systemd deve dire `DISCOVERY_FAILED`; su una macchina
    con systemd deve elencare unit vere. In nessuno dei due casi puo' rispondere
    «supportata, zero servizi» — che era la vecchia risposta sul primo.
    """
    from windows_os_api.backends.linux import LinuxBackend
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        backend = LinuxBackend(sandbox_root=tmp)
        out = discover(backend, "services", "services", backend.list_services)

    if _systemd_is_usable():
        assert out["supported"] is True, out
        assert "error_code" not in out, out
        assert out["services"], "systemd gira ma non e' stata elencata nessuna unit"
    else:
        assert out.get("error_code") == DISCOVERY_FAILED, (
            f"systemd non e' utilizzabile su questa macchina, ma l'API non lo "
            f"dice: {out}"
        )
        assert out["services"] == []


def test_one_scope_failing_does_not_hide_the_other(monkeypatch):
    """`--user` senza sessione e' normale: non deve annullare lo scope system.

    Se un fallimento parziale diventasse un DiscoveryFailed, ogni macchina senza
    sessione utente sembrerebbe rotta.
    """
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/systemctl")
    system_units = (
        "ssh.service loaded active running OpenSSH server\n"
        "cron.service loaded active running Regular background jobs\n"
    )

    def per_scope(argv, **kw):
        if "--user" in argv:
            return _Proc(stderr="Failed to connect to bus", returncode=1)
        return _Proc(stdout=system_units, returncode=0)

    rows = lsvc.list_services(run=per_scope)
    names = {r["name"] for r in rows}
    assert names == {"ssh", "cron"}, rows
    assert all(r["scope"] == "system" for r in rows), rows
