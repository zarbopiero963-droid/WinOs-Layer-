"""`power_action`: un rifiuto riconoscibile, e uguale sui due OS.

Seconda voce (P2) della ricognizione stub, issue #6.

Su Windows rispondeva:

    {"ok": False, "error": "power actions require interactive elevation"}

Incondizionato, e senza `denied` ne' `code`. Due problemi distinti:

1. **Un rifiuto di policy aveva la stessa forma di un guasto.** Senza
   `denied: True` e un codice, chi legge non sa se ha senso riprovare, se deve
   chiedere un permesso, o se il sistema si e' rotto.

2. **I due backend rispondevano diversamente alla stessa domanda.** Su Linux la
   stessa funzione usa gia' `deny_structured` con `code="hardware_protected"`.
   Un client scritto contro Linux non riconosceva il rifiuto su Windows — ed e'
   il difetto piu' insidioso dei due, perche' si manifesta solo cambiando OS.

Cosa questa PR NON fa
---------------------
Non implementa shutdown/reboot. E' una superficie privilegiata reale, e la sua
aggiunta e' una decisione dell'owner — non un dettaglio da far passare mentre si
sistema la forma di un messaggio. Qui cambia solo **come si rifiuta**.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from windows_os_api.backends import linux as linux_module
from windows_os_api.backends import windows as windows_module


REAL_BACKENDS = (windows_module.WindowsBackend, linux_module.LinuxBackend)


def test_both_backends_refuse_in_the_same_shape():
    """La stessa domanda deve avere la stessa forma di risposta sui due OS.

    Verificato sul codice e non sul comportamento perche' `WindowsBackend` non
    si istanzia su Linux: e' l'unico modo di controllare la simmetria da qui, e
    la simmetria e' proprio la cosa che mancava.
    """
    for backend in REAL_BACKENDS:
        source = inspect.getsource(backend.power_action)
        assert "deny_structured" in source, (
            f"{backend.__name__}.power_action non usa la forma di rifiuto "
            f"condivisa: un client scritto contro un OS non riconoscerebbe "
            f"il rifiuto sull'altro"
        )
        assert "hardware_protected" in source, backend.__name__


def test_the_windows_refusal_carries_denied_and_a_code():
    """`denied` e `code` sono cio' che distingue un rifiuto da un guasto."""
    from windows_os_api.core.security.privilege import deny_structured

    out = deny_structured("x", code="hardware_protected", detail={"action": "shutdown"})
    assert out["ok"] is False
    assert out["denied"] is True
    assert out["code"] == "hardware_protected"
    assert out["detail"]["action"] == "shutdown"


def test_the_old_unstructured_literal_is_gone():
    """La regressione diretta.

    Cercata sulle costanti eseguibili: la docstring del metodo cita il vecchio
    messaggio per spiegare cosa c'era prima, e un controllo testuale
    costringerebbe a cancellare la spiegazione per far passare il test — la
    stessa lezione di #27.
    """
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(windows_module.WindowsBackend.power_action)))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docs.add(id(body[0].value))
    literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
    ]
    assert "power actions require interactive elevation" not in literals, (
        "e' tornato il rifiuto piatto, senza denied ne' code"
    )


def test_no_power_surface_was_added():
    """Decisione owner: implementare shutdown/reboot e' una scelta sua.

    Verificato sul codice perche' il punto e' l'ASSENZA di una superficie: un
    test comportamentale non puo' dimostrare che una API non viene usata.
    """
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(windows_module.WindowsBackend.power_action)))
    used = (
        {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    )
    for api in ("InitiateSystemShutdown", "ExitWindowsEx", "InitiateShutdown", "SeShutdownPrivilege"):
        assert api not in used, (
            f"power_action usa {api}: implementare shutdown/reboot e' una "
            f"decisione dell'owner, non un effetto collaterale di questa patch"
        )
    # Nemmeno per la via traversa di un comando esterno.
    source = inspect.getsource(windows_module.WindowsBackend.power_action)
    for tool in ("shutdown.exe", "shutdown /", "subprocess"):
        assert tool not in source, f"power_action invoca {tool!r}"


@pytest.mark.parametrize("action", ["shutdown", "reboot", "logoff", "sleep", "hibernate"])
def test_every_action_is_refused_the_same_way(action):
    """Nessuna azione ha una scorciatoia: il rifiuto non dipende da quale sia.

    Un `if action == "sleep"` che passasse sarebbe una superficie privilegiata
    aggiunta di soppiatto.
    """
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(windows_module.WindowsBackend.power_action)))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.If)], (
        "power_action ha un ramo condizionale: il rifiuto deve valere per ogni "
        "azione, altrimenti qualcuna passa"
    )


def test_audio_devices_documents_why_it_is_empty():
    """P3: una lista vuota muta e' una trappola, anche se irraggiungibile.

    Via API il flag `audio: False` la intercetta (#28, D4-B), ma chi chiama il
    backend direttamente riceve `[]` e legge "nessun dispositivo audio". La
    docstring e' l'avviso; implementarlo richiederebbe pycaw, che l'owner ha
    deciso di non aggiungere.
    """
    doc = inspect.getdoc(windows_module.WindowsBackend.audio_devices) or ""
    assert "D4-B" in doc, "l'assenza deve rimandare alla decisione che la motiva"
    assert "CAPABILITY_NOT_SUPPORTED" in doc
