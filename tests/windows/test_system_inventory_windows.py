"""Servizi, stampanti e volumi contro il vero host Windows.

Prima di questa PR i tre metodi erano stub:

    list_services()  ->  [{"name": "WinOsApi", "status": "unknown", ...}]
    list_printers()  ->  []
    list_devices()   ->  []

Il primo era peggio degli altri due: **inventava un servizio che non esiste**.
Una lista vuota è poco informativa; una riga inventata è una risposta su cui il
chiamante agisce e sbaglia.

Questi test girano su `windows-latest`, cioè su un Windows vero: chiedono al
Service Control Manager e allo spooler reali. Le asserzioni si appoggiano solo a
cose che su QUALUNQUE Windows sono vere — l'esistenza dei servizi `EventLog` e
`Schedule`, la presenza del volume `C:` — mai al contenuto specifico del runner.
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.windows

if sys.platform != "win32":
    pytest.skip("requires real Windows", allow_module_level=True)

from windows_os_api.backends.windows import WindowsBackend  # noqa: E402


@pytest.fixture
def backend(tmp_path):
    return WindowsBackend(str(tmp_path))


# ---------------------------------------------------------------------------
# Servizi
# ---------------------------------------------------------------------------
def test_the_invented_service_is_gone(backend):
    """La regressione diretta: `WinOsApi` non deve più comparire.

    Non esiste come servizio Windows, quindi se torna nell'elenco è di nuovo
    hardcoded — non c'è modo che il Service Control Manager lo restituisca.
    """
    names = {s["name"] for s in backend.list_services()}
    assert "WinOsApi" not in names, (
        "il servizio inventato è tornato: list_services sta di nuovo restituendo "
        "una riga hardcoded invece di interrogare il Service Control Manager"
    )


def test_real_services_are_enumerated(backend):
    """Un Windows senza servizi non esiste: una lista vuota qui è un difetto."""
    services = backend.list_services()
    assert len(services) > 10, f"solo {len(services)} servizi: enumerazione fallita"


def test_services_that_exist_on_every_windows_are_present(backend):
    """Ancora dei nomi che non dipendono dal runner.

    `EventLog` (Windows Event Log) e `Schedule` (Task Scheduler) esistono su
    ogni installazione Windows dal 2000 in poi. Se mancano, non stiamo leggendo
    l'elenco vero.
    """
    names = {s["name"] for s in backend.list_services()}
    assert "EventLog" in names, f"EventLog assente fra {len(names)} servizi"
    assert "Schedule" in names, f"Schedule assente fra {len(names)} servizi"


def test_every_service_row_has_the_declared_shape(backend):
    for s in backend.list_services():
        assert s["name"], s
        assert "display_name" in s, s
        assert s["scope"] == "system", s
        assert isinstance(s["status"], str) and s["status"], s


def test_service_status_is_a_known_word_not_a_placeholder(backend):
    """Gli stati devono essere il vocabolario condiviso con systemd, non "unknown".

    Lo stub restituiva `"unknown"` per l'unico servizio che inventava. Qui gli
    stati arrivano dal SCM: almeno un servizio DEVE essere `running` — se il
    Service Control Manager risponde, per definizione sta girando qualcosa.
    """
    states = {s["status"] for s in backend.list_services()}
    assert "running" in states, f"nessun servizio running: {sorted(states)[:10]}"
    assert "unknown" not in states, "'unknown' è il vecchio segnaposto"


def test_enumeration_needs_no_elevation(backend):
    """Gira come utente normale del runner: nessun servizio viene aperto o toccato.

    Se questo test passasse solo da amministratore, l'endpoint sarebbe inutile
    nell'uso normale — ed è un endpoint di sola lettura.
    """
    assert backend.list_services(), "enumerazione vuota senza privilegi elevati"


# ---------------------------------------------------------------------------
# Volumi
# ---------------------------------------------------------------------------
def test_the_system_volume_is_reported(backend):
    """`C:` c'è su ogni runner Windows di GitHub."""
    ids = {d["id"] for d in backend.list_devices()}
    assert "C:" in ids, f"volume di sistema assente: {sorted(ids)}"


def test_device_rows_match_the_linux_shape(backend):
    """Stesso contratto di `/sys/block` su Linux: un solo client per due OS.

    `type` è `"block"` su entrambi di proposito: `list_devices` significa
    "dispositivi a blocchi", non "tutto ciò che ha un driver".
    """
    for d in backend.list_devices():
        assert d["type"] == "block", d
        assert d["id"] and d["name"], d
        assert d["media"], d
        assert d["status"] in ("present", "no_root_dir"), d


def test_device_status_is_measured_not_asserted(backend):
    """`status` non è più il letterale "ok" appiccicato a ogni riga.

    Su Linux lo era; qui non lo è mai stato perché la lista era vuota. In
    entrambi i casi ora la riga dichiara solo ciò che l'enumerazione sostiene.
    """
    statuses = {d["status"] for d in backend.list_devices()}
    assert "ok" not in statuses, "'ok' è la vecchia asserzione non misurata"


# ---------------------------------------------------------------------------
# Stampanti
# ---------------------------------------------------------------------------
def test_printer_rows_have_the_declared_shape(backend):
    """Zero stampanti è un esito legittimo; una riga malformata no.

    Il runner può non averne: non si asserisce che ce ne sia una, si asserisce
    che quelle riportate siano descritte per intero.
    """
    for p in backend.list_printers():
        assert p["name"], p
        assert isinstance(p["default"], bool), p
        assert isinstance(p["jobs"], int), p
        assert "port" in p and "driver" in p, p


def test_at_most_one_default_printer(backend):
    """`GetDefaultPrinter` ne indica una sola: due sarebbero un errore di confronto."""
    defaults = [p for p in backend.list_printers() if p["default"]]
    assert len(defaults) <= 1, f"più di una stampante predefinita: {defaults}"


def test_no_default_printer_is_not_an_error(backend):
    """Una macchina senza stampanti è normale e deve rispondere, non esplodere.

    `GetDefaultPrinter` solleva quando non c'è un default: se quell'eccezione
    non fosse catturata, l'assenza di stampanti diventerebbe assenza di
    risposta.
    """
    printers = backend.list_printers()
    assert isinstance(printers, list)


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------
def test_capabilities_report_services_and_printers(backend):
    """Su un Windows con pywin32 le due capability sono vere.

    Sono riportate separatamente perché arrivano da due moduli pywin32
    indipendenti: uno può mancare senza l'altro.
    """
    flags = backend.capability_flags()
    assert flags["services"] is True, flags
    assert flags["printers"] is True, flags


# ---------------------------------------------------------------------------
# Controllo servizi: dichiarato non implementato (decisione owner D5-B)
# ---------------------------------------------------------------------------
def test_service_control_is_declared_unsupported_on_the_real_backend(backend):
    """Su Windows vero: `supported: false`, e nessuna operazione privilegiata.

    Rispondeva "service control requires elevated pywin32" — un messaggio che
    sembra un problema di permessi risolvibile elevando il processo. Non lo era:
    la chiamata non tentava nulla, nemmeno da amministratore, e il runner GitHub
    gira elevato — quindi questo test lo dimostra su una macchina che i
    privilegi ce li ha.
    """
    from windows_os_api.os.capability import CAPABILITY_NOT_SUPPORTED

    out = backend.control_service("Spooler", "stop")
    assert out["ok"] is False, out
    assert out["supported"] is False, out
    assert out["error_code"] == CAPABILITY_NOT_SUPPORTED, out
    assert "elevat" not in out["error"].lower(), out


def test_listing_still_works_while_controlling_does_not(backend):
    """Le due capability sono separate, e si vede sul sistema vero.

    Elencare i servizi funziona (PR #27); controllarli no. Un flag solo direbbe
    «servizi: si'» e lascerebbe credere che anche start/stop vada.
    """
    flags = backend.capability_flags()
    assert flags["services"] is True, flags
    assert flags["service_control"] is False, flags
    assert len(backend.list_services()) > 10, "l'elenco deve continuare a funzionare"


def test_the_spooler_is_not_stopped_by_asking(backend):
    """La prova che conta: il servizio resta com'era.

    `Spooler` esiste su ogni Windows. Se `control_service` tentasse davvero
    l'operazione — e il runner e' elevato — lo stato cambierebbe.
    """
    before = {s["name"]: s["status"] for s in backend.list_services()}
    backend.control_service("Spooler", "stop")
    after = {s["name"]: s["status"] for s in backend.list_services()}
    if "Spooler" in before:
        assert after.get("Spooler") == before["Spooler"], (
            f"lo stato di Spooler e' cambiato: {before['Spooler']} -> {after.get('Spooler')}"
        )


# ---------------------------------------------------------------------------
# Sessioni e privilegi misurati (ricognizione stub, issue #6)
# ---------------------------------------------------------------------------
def test_sessions_come_from_the_system_not_from_a_literal(backend):
    """Restituiva `[{"id": 1, "user": <utente>, "state": "Active"}]`.

    Nessuno aveva misurato quella sessione. Su un Windows vero ce n'e' almeno
    una — quella in cui gira il runner — e i suoi campi vengono dal sistema.
    """
    sessions = backend.list_sessions()
    assert sessions, "nessuna sessione enumerata su una macchina in uso"
    for s in sessions:
        assert s["source"] == "wts", s
        assert isinstance(s["id"], int), s
        assert s["state"] and s["state"] != "Active", (
            f"'Active' con la A maiuscola era il letterale inventato: {s}"
        )
        assert "station" in s, s


def test_the_console_session_is_reported(backend):
    """La sessione 0 (Services) esiste su ogni Windows.

    Un'asserzione che non dipende da come e' configurato il runner.
    """
    ids = {s["id"] for s in backend.list_sessions()}
    assert 0 in ids, f"sessione 0 assente: {sorted(ids)}"


def test_the_admin_flag_is_measured_and_matches_the_elevated_runner(backend):
    """`admin` era il letterale `False`. Il runner GitHub gira ELEVATO.

    Quindi su questa macchina il vecchio valore era dimostrabilmente falso, ed
    e' la prova migliore che il campo andava misurato: non un caso di
    laboratorio, il caso in cui il test gira.
    """
    user = backend.list_users()[0]
    assert user["admin"] is not None, "il privilegio non e' stato misurato"
    assert isinstance(user["admin"], bool), user
    assert user["admin"] is True, (
        f"il runner GitHub gira elevato: admin dovrebbe essere True, e' {user['admin']!r}"
    )


def test_admin_and_elevated_are_reported_separately(backend):
    """Due domande diverse: appartenere al gruppo, e girare elevato adesso."""
    user = backend.list_users()[0]
    assert "admin" in user and "elevated" in user, user
    assert user["elevated"] is not None, user


def test_sessions_capability_is_declared(backend):
    flags = backend.capability_flags()
    assert flags["sessions"] is True, flags
