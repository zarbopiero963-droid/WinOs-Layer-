"""L'allowlist dei servizi: default-deny, nomi esatti, nessuna scorciatoia.

Decisione owner D1-B, issue #6 (2026-09-08), testualmente: «l'allowlist puo'
partire vuota: default-deny. Nessun servizio deve essere controllabile se non
esplicitamente autorizzato. Non usare ADMIN + flag come autorizzazione
implicita globale.»

Cosa c'era prima
----------------
`control_service` sanificava il nome dell'unit — niente metacaratteri di shell,
niente path traversal — e poi lo passava a `systemctl`. Quella sanificazione
impedisce di **iniettare** un comando, non di **fermare il servizio sbagliato**:
`ssh`, `firewalld`, `systemd-journald` sono tutti nomi di unit perfettamente
validi. L'unica difesa erano i permessi di systemd, cioe' qualcosa che sta fuori
da questo programma e che, se il processo gira da root, non c'e'.

La proprieta' che conta, e come e' testata
-------------------------------------------
Non basta che una richiesta non autorizzata **fallisca**: non deve **arrivare**
a `systemctl`. La differenza e' fra un rifiuto e un tentativo andato male, e si
verifica spiando `subprocess.run` e asserendo che non e' stato chiamato — non
guardando il valore di ritorno, che sarebbe uguale nei due casi.
"""
from __future__ import annotations

import ast
import inspect
import sys

import pytest

from windows_os_api.os.services import service as svc
from windows_os_api.os.services.allowlist import (
    ALLOWLIST_EMPTY,
    ENV_VAR,
    SERVICE_NOT_ALLOWED,
    ServiceRejected,
    allowed_services,
    check,
)


@pytest.fixture(autouse=True)
def clean_allowlist(monkeypatch):
    """Ogni test parte dal default reale: nessuna variabile impostata."""
    monkeypatch.delenv(ENV_VAR, raising=False)


@pytest.fixture
def linux_backend(monkeypatch, tmp_path):
    """Forza LinuxBackend — senza, gli spy passerebbero per il motivo sbagliato.

    `tests/conftest.py` impone `WINOS_BACKEND=fake` a tutta la suite. Con il
    backend fake, `control()` non raggiunge MAI `linux_services.subprocess.run`,
    quindi un'asserzione «systemctl non e' stato invocato» sarebbe vera anche se
    l'allowlist non esistesse. Ci sono cascato scrivendo questi test: quattro
    passavano senza provare niente.
    """
    if sys.platform == "win32":
        pytest.skip("allowlist systemd: percorso Linux")
    from windows_os_api.backends.factory import get_backend, reset_backend
    from windows_os_api.core.runtime.config import get_settings

    monkeypatch.setenv("WINOS_BACKEND", "linux")
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(tmp_path / "sandbox"))
    get_settings.cache_clear()
    reset_backend()
    backend = get_backend()
    assert backend.name == "linux", (
        f"presupposto del test: serve LinuxBackend, ottenuto {backend.name!r}"
    )
    yield backend
    get_settings.cache_clear()
    reset_backend()


class _Spy:
    """Registra se e con quali argomenti `subprocess.run` e' stato invocato."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, *a, **kw):
        self.calls.append(list(argv))
        raise AssertionError(
            f"systemctl e' stato invocato con {argv}: l'allowlist non ha fermato "
            f"la richiesta PRIMA di raggiungere il sistema"
        )


# ---------------------------------------------------------------------------
# Default-deny
# ---------------------------------------------------------------------------
def test_with_no_allowlist_nothing_is_controllable():
    """Il default richiesto dall'owner: la lista parte vuota e nega tutto."""
    assert allowed_services() == frozenset()
    for action in ("start", "stop", "restart", "enable", "disable", "status"):
        with pytest.raises(ServiceRejected) as exc:
            check("nginx", action)
        assert exc.value.code == ALLOWLIST_EMPTY


def test_an_empty_allowlist_is_reported_differently_from_a_miss():
    """«La lista e' vuota» e «non e' in lista» mandano l'operatore in due posti diversi.

    Il primo dice «devi configurare qualcosa»; il secondo lo farebbe cercare un
    errore di battitura in una lista che non esiste.
    """
    with pytest.raises(ServiceRejected) as empty:
        check("nginx", "stop")
    assert empty.value.code == ALLOWLIST_EMPTY

    import os
    os.environ[ENV_VAR] = "postgres"
    try:
        with pytest.raises(ServiceRejected) as miss:
            check("nginx", "stop")
        assert miss.value.code == SERVICE_NOT_ALLOWED
        assert "postgres" in str(miss.value), "il rifiuto deve dire cosa E' autorizzato"
    finally:
        del os.environ[ENV_VAR]


def test_a_blank_or_missing_name_is_refused(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "nginx")
    for bad in ("", "   ", None, 42, []):
        with pytest.raises(ServiceRejected):
            check(bad, "stop")


# ---------------------------------------------------------------------------
# Nomi esatti — niente glob, niente prefissi
# ---------------------------------------------------------------------------
def test_the_allowlist_authorises_exact_names_only(monkeypatch):
    """`nginx` non autorizza `nginx-proxy`.

    I glob sono il modo in cui un'allowlist diventa permissiva senza che nessuno
    se ne accorga: un `systemd-*` scritto per comodita' autorizzerebbe
    `systemd-journald`.
    """
    monkeypatch.setenv(ENV_VAR, "nginx")
    assert check("nginx", "stop") == "nginx"
    for other in ("nginx-proxy", "nginx2", "my-nginx", "NGINX", "nginx.socket"):
        with pytest.raises(ServiceRejected), pytest.MonkeyPatch.context():
            check(other, "stop")


def test_a_star_in_the_allowlist_is_not_a_wildcard(monkeypatch):
    """Scriverci un glob non lo fa funzionare come glob: autorizza il nome `*`.

    Meglio che non autorizzi niente piuttosto che autorizzi tutto — chi lo
    scrive se ne accorge subito, invece di scoprirlo dopo.
    """
    monkeypatch.setenv(ENV_VAR, "nginx*")
    with pytest.raises(ServiceRejected):
        check("nginx", "stop")
    with pytest.raises(ServiceRejected):
        check("nginx-proxy", "stop")


def test_the_service_suffix_is_the_same_service(monkeypatch):
    """`nginx` e `nginx.service` non devono divergere per un suffisso."""
    monkeypatch.setenv(ENV_VAR, "nginx")
    assert check("nginx.service", "stop") == "nginx"
    monkeypatch.setenv(ENV_VAR, "nginx.service")
    assert check("nginx", "stop") == "nginx"


def test_whitespace_and_empty_entries_are_ignored(monkeypatch):
    monkeypatch.setenv(ENV_VAR, " nginx , , postgres.service ,  ")
    assert allowed_services() == frozenset({"nginx", "postgres"})


def test_the_allowlist_is_read_now_not_frozen_at_import(monkeypatch):
    """Un'allowlist congelata all'import sarebbe diversa da quella impostata."""
    assert allowed_services() == frozenset()
    monkeypatch.setenv(ENV_VAR, "nginx")
    assert allowed_services() == frozenset({"nginx"})


# ---------------------------------------------------------------------------
# SECURITY BLOCK — la richiesta non deve ARRIVARE al sistema
# ---------------------------------------------------------------------------
def test_a_denied_service_never_reaches_systemctl(monkeypatch, linux_backend):
    """La proprieta' che conta davvero.

    Uno spy che solleva se invocato: se l'allowlist fosse controllata dopo, o
    non fosse controllata affatto, il test fallirebbe con l'argv reale in chiaro
    nel messaggio.
    """
    spy = _Spy()
    monkeypatch.setattr("windows_os_api.backends.linux_services.subprocess.run", spy)
    monkeypatch.setenv(ENV_VAR, "nginx")

    result = svc.control("ssh", "stop")

    assert result["ok"] is False
    assert result["denied"] is True
    assert result["code"] == SERVICE_NOT_ALLOWED
    assert spy.calls == [], f"systemctl invocato comunque: {spy.calls}"


def test_the_critical_units_are_refused_by_default(monkeypatch, linux_backend):
    """I nomi che l'owner ha citato: nessuno passa con la lista di default.

    Sono tutti nomi di unit validi che la sola sanificazione lasciava passare.
    """
    spy = _Spy()
    monkeypatch.setattr("windows_os_api.backends.linux_services.subprocess.run", spy)

    for unit in ("ssh", "sshd", "firewalld", "systemd-journald", "systemd-logind", "dbus"):
        result = svc.control(unit, "stop")
        assert result["ok"] is False, f"{unit} non e' stato rifiutato: {result}"
        assert result["denied"] is True, result
    assert spy.calls == [], f"systemctl invocato per: {spy.calls}"


def test_admin_is_not_a_shortcut_around_the_allowlist(monkeypatch, linux_backend):
    """Decisione esplicita dell'owner: ADMIN non autorizza implicitamente.

    L'allowlist e' consultata prima e indipendentemente dal ruolo — non c'e'
    nessun ramo che la salti, e questo test fallisce se ne comparisse uno.
    """
    spy = _Spy()
    monkeypatch.setattr("windows_os_api.backends.linux_services.subprocess.run", spy)
    monkeypatch.setenv("WINOS_ALLOW_PRIVILEGED", "true")
    monkeypatch.setenv("WINOS_ADMIN", "true")

    result = svc.control("ssh", "stop")
    assert result["denied"] is True, result
    assert spy.calls == []

    # Sul CODICE, non sul testo: la docstring di `control()` spiega proprio che
    # ADMIN non e' una scorciatoia, e un controllo testuale costringerebbe a
    # cancellare la spiegazione per far passare il test.
    tree = ast.parse(inspect.getsource(svc.control).lstrip())
    names = {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
    } | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    } | {
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    for shortcut in ("admin", "ADMIN", "privileged", "WINOS_ALLOW_PRIVILEGED"):
        assert shortcut not in names, (
            f"control() usa {shortcut!r} nel codice: il ruolo non deve entrare "
            f"nella decisione dell'allowlist"
        )


def test_an_allowed_service_does_reach_systemctl(monkeypatch, linux_backend):
    """Il gate autorizza, non blocca soltanto.

    Simmetrico del precedente: un'allowlist che nega tutto sarebbe «sicura» e
    inutile, e senza questo test un `return denied` incondizionato passerebbe.
    """
    calls = []

    class _Ok:
        returncode = 0
        stdout = "active"
        stderr = ""

    def fake_run(argv, *a, **kw):
        calls.append(list(argv))
        return _Ok()

    monkeypatch.setattr("windows_os_api.backends.linux_services.subprocess.run", fake_run)
    monkeypatch.setenv(ENV_VAR, "nginx")

    result = svc.control("nginx", "status", scope="user")
    assert result["ok"] is True, result
    assert calls, "il servizio autorizzato non ha raggiunto systemctl"
    assert calls[0] == ["systemctl", "--user", "status", "nginx.service"], calls[0]


def test_the_canonical_name_is_what_reaches_the_backend(monkeypatch, linux_backend):
    """`nginx.service` autorizzato arriva come `nginx.service`, una volta sola.

    Se il chiamante ri-normalizzasse per conto suo, le due normalizzazioni
    potrebbero divergere; qui si verifica che ne avvenga una sola.
    """
    calls = []

    class _Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "windows_os_api.backends.linux_services.subprocess.run",
        lambda argv, *a, **kw: (calls.append(list(argv)), _Ok())[1],
    )
    monkeypatch.setenv(ENV_VAR, "nginx")

    svc.control("nginx.service", "status", scope="user")
    assert calls[0][-1] == "nginx.service", calls[0]


# ---------------------------------------------------------------------------
# FAILURE / RECOVERY — l'allowlist non si indebolisce quando qualcosa va storto
# ---------------------------------------------------------------------------
def test_a_systemctl_failure_does_not_widen_the_allowlist(monkeypatch, linux_backend):
    """Un errore del sistema non deve trasformarsi in permesso.

    Se `systemctl` esplode su un servizio autorizzato, la risposta e' un errore
    su QUEL servizio; nulla in quel percorso deve rendere controllabile un
    servizio diverso.
    """
    def boom(argv, *a, **kw):
        raise OSError("systemctl sparito a meta' esecuzione")

    monkeypatch.setattr("windows_os_api.backends.linux_services.subprocess.run", boom)
    monkeypatch.setenv(ENV_VAR, "nginx")

    allowed = svc.control("nginx", "stop")
    assert allowed["ok"] is False
    assert "denied" not in allowed or allowed.get("code") != SERVICE_NOT_ALLOWED

    spy = _Spy()
    monkeypatch.setattr("windows_os_api.backends.linux_services.subprocess.run", spy)
    denied = svc.control("ssh", "stop")
    assert denied["denied"] is True
    assert spy.calls == []


def test_a_malformed_allowlist_denies_rather_than_allows(monkeypatch):
    """Fail-closed su una variabile scritta male.

    `WINOS_SERVICE_ALLOWLIST=","` non e' «autorizza tutto»: e' una lista vuota,
    quindi nega.
    """
    for junk in (",", ",,,", "   ", "\t", ", ,"):
        monkeypatch.setenv(ENV_VAR, junk)
        assert allowed_services() == frozenset(), junk
        with pytest.raises(ServiceRejected) as exc:
            check("nginx", "stop")
        assert exc.value.code == ALLOWLIST_EMPTY


def test_the_unit_name_sanitisation_still_applies(monkeypatch, linux_backend):
    """L'allowlist si aggiunge alla sanificazione, non la sostituisce.

    Un nome autorizzato ma malformato — che non dovrebbe esistere, ma che
    qualcuno potrebbe scrivere nella variabile — resta rifiutato dal backend.
    """
    spy_calls = []

    class _Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "windows_os_api.backends.linux_services.subprocess.run",
        lambda argv, *a, **kw: (spy_calls.append(list(argv)), _Ok())[1],
    )
    monkeypatch.setenv(ENV_VAR, "ngi;nx")

    result = svc.control("ngi;nx", "stop", scope="user")
    assert result["ok"] is False, result
    assert result.get("code") == "invalid_unit", result
    assert spy_calls == [], f"un nome malformato ha raggiunto systemctl: {spy_calls}"
