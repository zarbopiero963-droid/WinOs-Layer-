"""I flag di capability devono venire dal backend, non da una tabella parallela.

In `os/system/service.py` c'era una terza tabella, `elif backend == "windows"`,
scritta a mano. Era **irraggiungibile** — `WindowsBackend` espone
`capability_flags`, quindi il primo ramo la intercetta sempre — e dichiarava
`"services": False`, falso da quando il backend enumera davvero i servizi via il
Service Control Manager.

Una tabella morta che dice il falso e' peggio di nessuna tabella: nessuno la
corregge, perche' nessuno la vede sbagliare. Chi la leggesse cercando di capire
cosa supporta Windows troverebbe una risposta plausibile e obsoleta.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from windows_os_api.backends.linux import LinuxBackend
from windows_os_api.backends.windows import WindowsBackend
from windows_os_api.os.system import service as system_service


def test_the_real_backends_are_the_source_of_their_own_flags():
    """Il presupposto su cui poggia la rimozione della tabella morta.

    Se un giorno `WindowsBackend` perdesse `capability_flags`, il ramo hardcoded
    tornerebbe raggiungibile — e questo test avverte prima che succeda in
    silenzio.
    """
    assert hasattr(WindowsBackend, "capability_flags")
    assert hasattr(LinuxBackend, "capability_flags")


def test_no_hardcoded_windows_flag_table_comes_back():
    """La regressione diretta.

    Cercata come struttura e non come testo: il commento che spiega la
    rimozione nomina `windows` di proposito, e un controllo testuale
    costringerebbe a cancellare la spiegazione per passare.
    """
    tree = ast.parse(inspect.getsource(system_service.capabilities))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and comparator.value == "windows":
                pytest.fail(
                    "e' tornato un ramo che decide i flag dal nome 'windows': "
                    "e' irraggiungibile (WindowsBackend ha capability_flags) e "
                    "diventera' obsoleto senza che nessuno se ne accorga"
                )


@pytest.mark.parametrize("family", ["services", "audio", "devices", "printers"])
def test_both_real_backends_declare_the_four_read_only_families(family):
    """Un flag assente e' trattato come «supportata», quindi deve esserci.

    Senza il flag, un backend che NON puo' fare una cosa risponderebbe
    `supported: true` e poi una lista vuota — cioe' di nuovo la vecchia
    ambiguita', per la via piu' silenziosa.
    """
    for backend_cls in (WindowsBackend, LinuxBackend):
        source = inspect.getsource(backend_cls._probe_capabilities)
        assert f'"{family}"' in source, (
            f"{backend_cls.__name__} non dichiara il flag {family!r}: "
            f"la sua assenza verrebbe letta come 'supportata'"
        )


def test_windows_declares_audio_as_never_implemented():
    """Decisione owner D4-B, come dato e non come commento.

    `audio: False` da solo direbbe «qui non c'e' l'audio». La verita' e' che
    questo backend non lo implementa affatto senza pycaw, e la distinzione
    arriva al chiamante solo se il backend la dichiara.
    """
    assert "audio" in WindowsBackend.NOT_IMPLEMENTED


def test_linux_does_not_claim_anything_is_permanently_unimplemented():
    """Su Linux audio e stampanti ci sono: quando mancano, mancano gli strumenti.

    Dichiararle non implementate direbbe al chiamante di arrendersi quando
    invece basta installare un pacchetto.
    """
    never = getattr(LinuxBackend, "NOT_IMPLEMENTED", frozenset())
    assert "audio" not in never
    assert "printers" not in never
