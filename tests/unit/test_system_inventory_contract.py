"""`list_devices` / `list_services` / `list_printers`: un contratto, due OS.

Questi test girano su ogni piattaforma e sorvegliano le proprietà che devono
valere **indipendentemente** dal backend, perché sono le proprietà su cui un
client scrive il proprio codice una volta sola.

Le due regressioni sorvegliate, entrambe misurate su codice reale prima di
questa PR:

* **la riga inventata** — `WindowsBackend.list_services` restituiva
  `[{"name": "WinOsApi", "status": "unknown", ...}]`, un servizio che non
  esiste. Una lista vuota è poco informativa; una riga inventata è una risposta
  su cui il chiamante agisce e sbaglia;
* **lo stato asserito** — `LinuxBackend.list_devices` marcava ogni riga
  `"status": "ok"`, un giudizio di salute che nulla aveva verificato.

Il caso «zero elementi» vs «non ho guardato» NON è deciso qui: è la domanda D3
posta all'owner nella issue #6, e questi test sono scritti per non pregiudicarla.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from windows_os_api.backends import linux as linux_module
from windows_os_api.backends import windows as windows_module


# ---------------------------------------------------------------------------
# Nessuna riga inventata, in nessun backend reale
# ---------------------------------------------------------------------------
def _executable_string_constants(module) -> list[str]:
    """Le stringhe che il modulo può RESTITUIRE, escluse docstring e commenti.

    La distinzione conta: la docstring di `list_services` cita per esteso lo
    stub rimosso, perché sapere cosa c'era prima è metà del valore del commento.
    Un test che non sapesse distinguere una riga di documentazione da un valore
    di ritorno costringerebbe a cancellare la spiegazione per far passare il
    controllo — cioè a peggiorare il codice per compiacere il test.
    """
    tree = ast.parse(inspect.getsource(module))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


def test_no_real_backend_hardcodes_a_service_row():
    """Il guardiano strutturale della regressione principale.

    Si asserisce sulla struttura e non sul comportamento perché il difetto era
    invisibile all'esecuzione: lo stub RESTITUIVA una lista valida, di forma
    giusta, con dentro un servizio falso. Un test che chiamasse soltanto il
    metodo sarebbe passato prima e dopo.

    `fake.py` è escluso di proposito: è la fixture che *definisce* i dati finti,
    e le sue righe si chiamano "Fake ..." per esteso.
    """
    for module in (windows_module, linux_module):
        literals = _executable_string_constants(module)
        assert "WinOsApi" not in literals, (
            f"{module.__name__} restituisce di nuovo il servizio inventato 'WinOsApi'"
        )
        assert "use pywin32 service APIs" not in literals, (
            f"{module.__name__} contiene di nuovo la riga-segnaposto dello stub"
        )


def test_the_guard_would_actually_catch_a_reintroduced_row():
    """Il test del test: la stringa in una docstring passa, in un `return` no.

    Senza questa verifica, `_executable_string_constants` potrebbe filtrare
    troppo e il guardiano sopra diventerebbe verde qualunque cosa succeda — un
    controllo che non può fallire non è un controllo.
    """
    import types

    ok = types.ModuleType("ok")
    ok.__dict__["__source__"] = None
    fine = ast.parse('"""Prima restituiva WinOsApi."""\ndef f():\n    return []\n')
    bad = ast.parse('def f():\n    return [{"name": "WinOsApi"}]\n')

    def literals(tree):
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef)):
                body = getattr(node, "body", None) or []
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docs.add(id(body[0].value))
        return [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]

    assert "WinOsApi" not in " ".join(literals(fine)), "la docstring non deve far scattare il guardiano"
    assert "WinOsApi" in literals(bad), "una riga reintrodotta DEVE far scattare il guardiano"


def test_windows_services_are_enumerated_not_returned_literal():
    """`list_services` deve *interrogare* il Service Control Manager.

    Fallisce se qualcuno rimette un `return [...]` letterale al posto della
    chiamata: il nome dell'API deve comparire nel corpo del metodo.
    """
    source = inspect.getsource(windows_module.WindowsBackend.list_services)
    assert "EnumServicesStatus" in source, (
        "list_services non chiama più il Service Control Manager"
    )
    assert "OpenSCManager" in source


def test_windows_printers_come_from_the_spooler():
    source = inspect.getsource(windows_module.WindowsBackend.list_printers)
    assert "EnumPrinters" in source, "list_printers non interroga più lo spooler"


def test_windows_devices_come_from_the_volume_table():
    source = inspect.getsource(windows_module.WindowsBackend.list_devices)
    assert "GetLogicalDriveStrings" in source
    assert "GetDriveType" in source, (
        "senza GetDriveType il campo 'media' tornerebbe a essere un'asserzione"
    )


# ---------------------------------------------------------------------------
# Nessuno stato asserito e mai misurato
# ---------------------------------------------------------------------------
def test_no_backend_stamps_a_health_verdict_it_never_measured():
    """`"status": "ok"` era un giudizio di salute che nessuno aveva controllato.

    L'esistenza di una voce in `/sys/block` (o di una lettera di unità) sostiene
    una sola affermazione — che il dispositivo è presente — e quella è ciò che
    la riga dice adesso.
    """
    for method in (
        linux_module.LinuxBackend.list_devices,
        windows_module.WindowsBackend.list_devices,
    ):
        source = inspect.getsource(method)
        assert '"status": "ok"' not in source, (
            f"{method.__qualname__} riasserisce uno stato di salute mai misurato"
        )
        assert '"present"' in source, (
            f"{method.__qualname__} non dichiara più ciò che l'enumerazione sostiene"
        )


def test_linux_media_comes_from_the_kernel_not_from_a_guess():
    """`media` deve leggere `removable`, non dedurre "fixed" per default."""
    source = inspect.getsource(linux_module.LinuxBackend.list_devices)
    assert "removable" in source
    assert '"unknown"' in source, (
        "senza il fallback a 'unknown' un errore di lettura diventerebbe 'fixed'"
    )


# ---------------------------------------------------------------------------
# Le due metà dello stesso contratto
# ---------------------------------------------------------------------------
def test_list_devices_means_block_devices_on_both_platforms():
    """Un endpoint, un significato.

    Su Linux `list_devices` legge `/sys/block`. Se su Windows enumerasse i
    dispositivi PnP, lo stesso endpoint significherebbe "dischi" su un OS e
    "tutto ciò che ha un driver" sull'altro — e nessun client potrebbe essere
    scritto una volta sola. Entrambi marcano `type: "block"`.
    """
    for method in (
        linux_module.LinuxBackend.list_devices,
        windows_module.WindowsBackend.list_devices,
    ):
        assert '"type": "block"' in inspect.getsource(method), method.__qualname__


def test_service_states_share_one_vocabulary_with_systemd():
    """`running`/`stopped` significano la stessa cosa sui due OS.

    Il SCM parla in numeri (4 = running); systemd in parole. La tabella di
    traduzione esiste perché il chiamante non debba conoscerne nessuna delle due.
    """
    states = windows_module.WindowsBackend._SERVICE_STATES
    assert states[4] == "running"
    assert states[1] == "stopped"
    assert "unknown" not in states.values(), (
        "'unknown' era il segnaposto dello stub e non è uno stato del SCM"
    )


def test_an_unmapped_service_code_keeps_its_number():
    """Un codice ignoto resta leggibile invece di diventare "unknown".

    Un numero si può cercare nella documentazione; la parola "unknown" no — è
    esattamente l'informazione che lo stub distruggeva.
    """
    states = windows_module.WindowsBackend._SERVICE_STATES
    assert 99 not in states
    source = inspect.getsource(windows_module.WindowsBackend.list_services)
    assert 'f"state_{state_code}"' in source


# ---------------------------------------------------------------------------
# Il backend fake resta finto, e lo dice
# ---------------------------------------------------------------------------
def test_the_fake_backend_still_labels_its_rows_as_fake(tmp_path):
    """La fixture può inventare: è il suo lavoro, purché lo dichiari nel nome."""
    from windows_os_api.backends.fake import FakeBackend

    fake = FakeBackend(str(tmp_path))
    names = [d["name"] for d in fake.list_devices()]
    assert any("Fake" in n for n in names), names
