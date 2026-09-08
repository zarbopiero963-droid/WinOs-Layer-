"""Sessioni e privilegi: misurati, non asseriti.

Due difetti trovati con la ricognizione degli stub chiesta dall'owner, entrambi
su `WindowsBackend` e entrambi su campi che riguardano **chi e' connesso alla
macchina e con quali poteri**:

    def list_sessions(self):
        return [{"id": 1, "user": <utente corrente>, "state": "Active"}]

    def list_users(self):
        return [{..., "admin": False}]

La prima riga e' inventata: nessuno aveva misurato quella sessione ne' quello
stato. La seconda e' peggio nel merito — `admin: False` su una sessione elevata
e' un'affermazione **falsa su una proprieta' di sicurezza**, e il runner CI di
GitHub gira elevato, quindi il caso non e' teorico.

Perche' `None` e non `False`
-----------------------------
Un privilegio che non si riesce a misurare deve essere `None`. `False` significa
«ho guardato e non ce l'ha», ed e' la risposta su cui un chiamante decide di
procedere. Sbagliarla in quella direzione e' il fail-open.

`admin` e `elevated` sono due cose
-----------------------------------
`admin` = l'utente appartiene al gruppo Administrators (la stessa domanda a cui
risponde `u.name == "root"` su Linux). `elevated` = il processo sta girando
elevato adesso. Un amministratore che lancia un processo non elevato e' `admin`
ma non `elevated`, ed e' esattamente il caso in cui il vecchio `False` sembrava
plausibile.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from windows_os_api.backends import linux as linux_module
from windows_os_api.backends import windows as windows_module


def _executable_literals(fn) -> list:
    """Le costanti che la funzione puo' RESTITUIRE, escluse le docstring.

    Serve per la stessa ragione di #27: le docstring di questi metodi citano il
    valore rimosso per spiegare cosa c'era prima, e un controllo testuale
    costringerebbe a cancellare la spiegazione per far passare il test.
    """
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docs.add(id(body[0].value))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and id(n) not in docs]


# ---------------------------------------------------------------------------
# `admin` non e' un letterale
# ---------------------------------------------------------------------------
def test_no_backend_asserts_a_privilege_it_never_measured():
    """La regressione diretta: `"admin": False` scritto a mano.

    Cercato come struttura — una chiave `admin` il cui valore e' una costante —
    perche' il difetto era invisibile all'esecuzione: il metodo restituiva un
    dizionario di forma giusta con dentro un'affermazione falsa.
    """
    import textwrap

    for fn in (windows_module.WindowsBackend.list_users, linux_module.LinuxBackend.list_users):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "admin":
                    assert not isinstance(value, ast.Constant), (
                        f"{fn.__qualname__} asserisce admin={value.value!r} invece di "
                        f"misurarlo: e' un'affermazione su una proprieta' di sicurezza"
                    )


def test_windows_measures_group_membership_not_process_elevation():
    """`admin` risponde alla stessa domanda su entrambi gli OS.

    Su Linux e' «e' root»; su Windows deve essere «e' nel gruppo
    Administrators», non «il processo e' elevato adesso» — che e' un'altra cosa
    e ha un campo suo.
    """
    source = inspect.getsource(windows_module.WindowsBackend._is_administrator)
    assert "CheckTokenMembership" in source
    assert "IsUserAnAdmin" not in source, (
        "IsUserAnAdmin risponde sull'elevazione del PROCESSO, non "
        "sull'appartenenza al gruppo: e' il campo `elevated`"
    )


def test_an_unmeasurable_privilege_is_none_not_false():
    """`None` significa «non lo so»; `False` significa «ho guardato».

    Sbagliare in direzione di `False` e' il fail-open: e' la risposta su cui il
    chiamante decide di procedere.
    """
    for name in ("_is_administrator", "_is_elevated"):
        source = inspect.getsource(getattr(windows_module.WindowsBackend, name))
        assert "return None" in source, (
            f"{name} non ha un percorso che dichiara «non misurabile»"
        )
        assert "return False" not in source, (
            f"{name} restituisce False dove non ha potuto misurare"
        )


# ---------------------------------------------------------------------------
# Le sessioni non si inventano
# ---------------------------------------------------------------------------
def test_windows_sessions_are_enumerated_not_fabricated():
    source = inspect.getsource(windows_module.WindowsBackend.list_sessions)
    assert "WTSEnumerateSessions" in source, (
        "list_sessions non interroga piu' il sistema"
    )


def test_the_fabricated_session_row_cannot_come_back():
    """`{"id": 1, "state": "Active"}` non deve tornare come letterale."""
    import textwrap

    tree = ast.parse(textwrap.dedent(
        inspect.getsource(windows_module.WindowsBackend.list_sessions)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        pairs = {
            k.value: v for k, v in zip(node.keys, node.values)
            if isinstance(k, ast.Constant)
        }
        if "id" in pairs and isinstance(pairs["id"], ast.Constant):
            pytest.fail(
                f"list_sessions costruisce una sessione con id costante "
                f"{pairs['id'].value!r}: nessuno l'ha misurata"
            )
        if "state" in pairs and isinstance(pairs["state"], ast.Constant):
            pytest.fail("list_sessions asserisce uno stato costante")


def test_an_unknown_session_state_keeps_its_number():
    """Un codice ignoto resta cercabile invece di diventare "unknown"."""
    states = windows_module.WindowsBackend._WTS_STATES
    assert states[0] == "active"
    assert states[4] == "disconnected"
    assert 99 not in states
    assert 'f"state_{state_code}"' in inspect.getsource(
        windows_module.WindowsBackend.list_sessions
    )


def test_a_session_whose_owner_cannot_be_read_is_not_given_one():
    """L'utente di una sessione altrui puo' non essere leggibile.

    In quel caso si riporta la sessione con proprietario vuoto: inventarlo
    sarebbe attribuire a qualcuno una sessione che non e' sua.
    """
    source = inspect.getsource(windows_module.WindowsBackend.list_sessions)
    assert "WTSQuerySessionInformation" in source
    assert 'user = ""' in source


# ---------------------------------------------------------------------------
# Il difetto gemello su Linux
# ---------------------------------------------------------------------------
def test_linux_derived_sessions_do_not_claim_a_measured_state():
    """Il fallback sintetizza righe dagli utenti: non e' un'enumerazione.

    Marcava ogni riga `"state": "Active"` — uno stato mai misurato, appiccicato
    a righe derivate. E' la stessa cosa del `"status": "ok"` tolto in #27.
    """
    source = inspect.getsource(linux_module.LinuxBackend.list_sessions)
    assert '"state": "Active"' not in source, (
        "il fallback derivato riasserisce uno stato mai misurato"
    )
    assert '"derived": True' in source, (
        "le righe sintetizzate devono dichiarare di esserlo"
    )
    assert '"source": "psutil"' in source


def test_real_linux_sessions_keep_their_measured_state():
    """La correzione riguarda SOLO il fallback: `loginctl` misura davvero.

    Se togliesse lo stato anche alle sessioni vere, il difetto sarebbe stato
    scambiato per un altro.
    """
    source = inspect.getsource(linux_module.LinuxBackend.list_sessions)
    assert "parse_loginctl_sessions" in source
    assert "loginctl" in source
