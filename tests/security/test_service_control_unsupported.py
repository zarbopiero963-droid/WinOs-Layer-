"""Capability vs allowlist for service control (D5-B order + N007 Windows SCM).

Storico D5-B (issue #6): Windows dichiarava ``service_control`` non supportato
invece di uno stub «requires elevation». N007 (#67 / H63-N007) implementa lo
SCM reale dietro allowlist vuota (D1-B); questi test restano sul contratto
dell'ordine capability→allowlist e sulla distinzione 501 vs 403 per i backend
che ancora NON supportano il controllo.

Cosa c'era prima
----------------
    return {"ok": False, "error": "service control requires elevated pywin32"}

Restituito **incondizionatamente**. Il messaggio sembra un problema di permessi
risolvibile elevando il processo; non lo era — la chiamata non tentava nulla,
nemmeno da amministratore. Chi lo leggeva cercava la causa dalla parte
sbagliata, e l'allowlist introdotta in #29 gattava su Windows una porta che non
si apre.

Le due domande, e le due risposte
----------------------------------
    "non ti e' permesso"   ->  403, allowlist   ->  puoi chiedere l'autorizzazione
    "non so farlo"         ->  501, capability  ->  non c'e' niente da chiedere

Tenerle distinte e' il punto della decisione. Un unico codice manderebbe
l'operatore a configurare un'allowlist che non cambierebbe niente.

L'ordine conta
--------------
La capability e' controllata PRIMA dell'allowlist. Su un backend che non
implementa il controllo, «non e' in allowlist» sarebbe fuorviante: suggerirebbe
che aggiungendolo funzionerebbe. Il motivo piu' fondamentale va detto per primo.
"""
from __future__ import annotations

import inspect

import pytest

from windows_os_api.os.capability import CAPABILITY_NOT_SUPPORTED, CAPABILITY_UNAVAILABLE
from windows_os_api.os.services import service as svc
from windows_os_api.os.services.allowlist import ENV_VAR, SERVICE_NOT_ALLOWED


@pytest.fixture(autouse=True)
def clean_allowlist(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)


class _NeverControls:
    """Un backend che non implementa il controllo — e che esplode se chiamato."""

    name = "windows"
    NOT_IMPLEMENTED = frozenset({"service_control"})
    touched: list = []

    def capability_flags(self):
        return {"services": True, "service_control": False}

    def control_service(self, name, action, **kw):
        self.touched.append((name, action))
        raise AssertionError(
            f"il backend e' stato chiamato per {name!r}/{action!r}: la capability "
            f"doveva essere dichiarata non supportata PRIMA di arrivare qui"
        )


@pytest.fixture
def not_supported(monkeypatch):
    backend = _NeverControls()
    backend.touched = []
    monkeypatch.setattr("windows_os_api.os.services.service.get_backend", lambda: backend)
    return backend


# ---------------------------------------------------------------------------
# La dichiarazione
# ---------------------------------------------------------------------------
def test_the_refusal_says_not_implemented_not_denied(not_supported):
    out = svc.control("nginx", "stop")
    assert out["ok"] is False
    assert out["supported"] is False, out
    assert out["error_code"] == CAPABILITY_NOT_SUPPORTED, out
    assert "non e' una questione di configurazione" in out["reason"], out


def test_no_privileged_operation_is_attempted(not_supported):
    """Il requisito esplicito dell'owner: nessuna operazione privilegiata.

    Il backend solleva se toccato: se la capability fosse controllata dopo, o
    non fosse controllata, il test fallirebbe col nome del servizio in chiaro.
    """
    for action in ("start", "stop", "restart", "enable", "disable", "status"):
        out = svc.control("spooler", action)
        assert out["supported"] is False, out
    assert not_supported.touched == [], not_supported.touched


def test_it_is_not_reported_as_an_allowlist_refusal(not_supported, monkeypatch):
    """Anche col servizio ESPLICITAMENTE autorizzato la risposta non cambia.

    E' la prova che l'ordine e' quello giusto: se l'allowlist venisse prima,
    autorizzare `nginx` cambierebbe la risposta e suggerirebbe che ora funziona.
    """
    monkeypatch.setenv(ENV_VAR, "nginx")
    out = svc.control("nginx", "stop")
    assert out["error_code"] == CAPABILITY_NOT_SUPPORTED, out
    assert out.get("code") != SERVICE_NOT_ALLOWED, out
    assert not_supported.touched == []


def test_the_message_no_longer_blames_elevation(not_supported):
    """Il messaggio vecchio mandava a cercare la causa dalla parte sbagliata."""
    out = svc.control("nginx", "stop")
    assert "elevat" not in out["error"].lower(), out
    assert "pywin32" not in out["error"].lower(), out


# ---------------------------------------------------------------------------
# Non si e' aggiunta nessuna superficie privilegiata
# ---------------------------------------------------------------------------
def test_windows_scm_control_lives_in_windows_services_module():
    """N007: real SCM calls are in windows_services, reached via the backend.

    The old D5-B stub forbade OpenService in WindowsBackend.control_service.
    N007 implements them in ``windows_services.control_service`` and the
    backend delegates — still behind D1-B empty allowlist + RBAC.
    """
    from windows_os_api.backends import windows as win
    from windows_os_api.backends import windows_services as wsvc

    source = inspect.getsource(win.WindowsBackend.control_service)
    assert "windows_services" in source
    assert "service control requires elevated pywin32" not in source
    assert hasattr(wsvc, "control_service")
    module_src = inspect.getsource(wsvc)
    for api in ("OpenService", "ControlService", "StartService", "QueryServiceStatus"):
        assert api in module_src, api


def test_the_old_stub_message_is_gone():
    from windows_os_api.backends import windows as win

    source = inspect.getsource(win.WindowsBackend.control_service)
    assert "service control requires elevated pywin32" not in source


def test_windows_no_longer_lists_service_control_as_never_implemented():
    """N007: service_control is implemented when win32service is present."""
    from windows_os_api.backends.windows import WindowsBackend

    assert "service_control" not in WindowsBackend.NOT_IMPLEMENTED
    # N009: audio read is implemented (WASAPI); no longer NEVER-implemented.
    assert "audio" not in WindowsBackend.NOT_IMPLEMENTED


def test_listing_and_controlling_are_separate_capabilities():
    """Two flags remain: enumeration vs control, both gated on win32service.

    A single flag would still blur the two surfaces; N007 turns control on
    without collapsing the distinction.
    """
    from windows_os_api.backends.windows import WindowsBackend

    source = inspect.getsource(WindowsBackend._probe_capabilities)
    assert '"services"' in source
    assert '"service_control": self._win32service is not None' in source


def test_linux_says_unavailable_not_unsupported_when_systemctl_is_missing():
    """Su Linux il controllo E' implementato: se manca lo strumento, e' installabile.

    La differenza dice all'operatore se ha senso installare qualcosa — che e'
    la stessa distinzione introdotta in #28 fra i due codici.
    """
    from windows_os_api.backends.linux import LinuxBackend

    never = getattr(LinuxBackend, "NOT_IMPLEMENTED", frozenset())
    assert "service_control" not in never

    source = inspect.getsource(LinuxBackend._probe_capabilities)
    assert '"service_control": has_systemctl' in source


# ---------------------------------------------------------------------------
# Il gate resta il gate dove il controllo E' supportato
# ---------------------------------------------------------------------------
def test_a_supporting_backend_still_goes_through_the_allowlist(monkeypatch):
    """La capability non scavalca l'allowlist: la precede soltanto.

    Senza questo, spostare il controllo di capability davanti avrebbe potuto
    saltare il gate su Linux e nessuno se ne sarebbe accorto.
    """
    calls = []

    class _Supports:
        name = "linux"
        NOT_IMPLEMENTED = frozenset()

        def capability_flags(self):
            return {"service_control": True}

        def control_service(self, name, action, **kw):
            calls.append((name, action))
            return {"ok": True, "name": name, "action": action}

    monkeypatch.setattr("windows_os_api.os.services.service.get_backend", lambda: _Supports())

    denied = svc.control("ssh", "stop")
    assert denied["denied"] is True, denied
    assert denied["code"] == "SERVICE_ALLOWLIST_EMPTY", denied
    assert calls == [], calls

    monkeypatch.setenv(ENV_VAR, "ssh")
    allowed = svc.control("ssh", "stop")
    assert allowed["ok"] is True, allowed
    assert calls == [("ssh", "stop")], calls


def test_a_backend_without_the_flag_is_not_treated_as_unsupported(monkeypatch):
    """L'assenza del flag non e' un «no».

    Dedurne `false` renderebbe non supportato ogni backend che non tenga quel
    flag — la stessa invenzione che il contratto `supported` esiste per togliere.
    """
    class _Bare:
        name = "bare"

        def control_service(self, name, action, **kw):
            return {"ok": True}

    monkeypatch.setattr("windows_os_api.os.services.service.get_backend", lambda: _Bare())
    monkeypatch.setenv(ENV_VAR, "nginx")
    out = svc.control("nginx", "stop")
    assert out.get("supported") is not False, out
    assert out["ok"] is True, out
