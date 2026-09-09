"""L'imbragatura X non deve poter appendere il job.

Il difetto, misurato su CI (`test-linux`, run 34276587508)::

    tests/linux/conftest.py:275: in event_recorder
        subprocess.run(['xdotool', 'windowsize', '--sync', '4194305', '900', '700'], ...)
    subprocess.TimeoutExpired: ... timed out after 10 seconds
    145 passed, 15 skipped, 598 deselected, 1 error

Un `--sync` aspetta che il window manager confermi la modifica: e' quello che lo
rende utile, ed e' quello che lo fa restare appeso quando il window manager e'
occupato e non risponde. `subprocess.run(..., timeout=...)` allora SOLLEVA da
dentro una fixture, e un solo `windowsize` in stallo si porta via l'intero job —
mentre nessun test stava misurando alcunche' di sbagliato.

Riprodotto in modo deterministico con `SIGSTOP` su openbox: `windowmove --sync`
e `windowsize --sync` restano fermi per tutto il timeout, mentre un semplice
`getwindowgeometry` risponde in 0.00s. E' il caso limite di un runner carico.

Questi test coprono le due direzioni:

* PASS — gli helper rispondono quando il sistema risponde;
* BLOCK — quando NON risponde, l'helper torna `None`/`{}`/`False` invece di
  sollevare, e la fixture puo' decidere misurando invece che fidandosi.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.linux

if sys.platform == "win32":
    pytest.skip("Linux only", allow_module_level=True)

from tests.linux.conftest import (  # noqa: E402
    MAX_AIM_OFFSET,
    MIN_RECORDER_SIDE,
    require_wm_tools,
    wait_for_focus,
    wait_until_managed,
    window_geometry,
    x_env,
    xdo,
)


# ---------------------------------------------------------------------------
# `xdo`: un xdotool appeso torna None, non solleva
# ---------------------------------------------------------------------------
def test_a_hung_xdotool_comes_back_as_none_instead_of_raising():
    """La regressione diretta: prima questo era un `TimeoutExpired` in faccia.

    `xdotool sleep` e' un comando vero di xdotool e dura quanto gli si dice, per
    cui il caso «il processo non finisce entro il timeout» e' riproducibile qui
    senza dipendere da come e' fatto il window manager oggi.
    """
    require_wm_tools()
    started = time.time()
    assert xdo(["sleep", "5"], timeout=1.0) is None, (
        "un xdotool che supera il timeout deve tornare None: se solleva, "
        "l'eccezione esce da dentro una fixture e porta via il job"
    )
    elapsed = time.time() - started
    assert elapsed < 4.0, f"il timeout non ha interrotto nulla: {elapsed:.1f}s"


def test_xdo_returns_the_real_result_when_the_call_succeeds():
    """Il ramo felice resta un risultato vero, non un `None` di comodo."""
    require_wm_tools()
    result = xdo(["getdisplaygeometry"], timeout=5)
    assert result is not None, "getdisplaygeometry non ha risposto sul display di test"
    assert result.returncode == 0, result
    assert len(result.stdout.split()) == 2, result.stdout


def test_xdo_reports_a_failing_command_without_raising():
    """Uscita diversa da zero non e' un'eccezione: e' un dato."""
    require_wm_tools()
    result = xdo(["getwindowgeometry", "--shell", "99999999"], timeout=5)
    assert result is not None
    assert result.returncode != 0, result


# ---------------------------------------------------------------------------
# `window_geometry`: misura, o dichiara di non sapere
# ---------------------------------------------------------------------------
def test_geometry_of_a_window_that_does_not_exist_is_empty():
    require_wm_tools()
    assert window_geometry(99999999) == {}


def test_geometry_of_a_real_window_has_all_four_numbers(probe_window):
    rect = window_geometry(probe_window.hwnd)
    assert set(rect) == {"x", "y", "width", "height"}, rect
    assert rect["width"] > 0 and rect["height"] > 0, rect


# ---------------------------------------------------------------------------
# `wait_until_managed`: attende una condizione, e si arrende in tempo
# ---------------------------------------------------------------------------
def test_a_title_that_never_appears_returns_false_within_the_deadline():
    """Non deve aspettare per sempre una finestra che non arrivera' mai."""
    require_wm_tools()
    started = time.time()
    assert wait_until_managed("winos-title-che-non-esiste-mai", timeout=1.0) is False
    elapsed = time.time() - started
    assert elapsed < 4.0, f"la scadenza non e' stata rispettata: {elapsed:.1f}s"


def test_a_real_window_is_reported_as_managed(probe_window):
    assert wait_until_managed(probe_window.title, timeout=10.0) is True


# ---------------------------------------------------------------------------
# `wait_for_focus`: il fuoco si rilegge, non si presume
# ---------------------------------------------------------------------------
def test_focus_on_a_window_that_never_gets_it_returns_false():
    require_wm_tools()
    started = time.time()
    assert wait_for_focus(99999999, timeout=1.0) is False
    assert time.time() - started < 4.0


def test_focus_is_confirmed_after_activating_a_real_window(probe_window):
    xdo(["windowactivate", str(probe_window.hwnd)], timeout=5)
    assert wait_for_focus(probe_window.hwnd, timeout=5.0) is True


# ---------------------------------------------------------------------------
# Il caso di CI: window manager vivo ma che non risponde
# ---------------------------------------------------------------------------
def _openbox_pids() -> list[int]:
    result = subprocess.run(  # noqa: S603
        ["pgrep", "-f", "^openbox"], capture_output=True, text=True,
        timeout=5, check=False,
    )
    return [int(p) for p in result.stdout.split() if p.isdigit()]


def test_a_window_manager_that_does_not_answer_does_not_take_the_job_down(probe_window):
    """La riproduzione esatta del fallimento di CI, con openbox fermato.

    Con il window manager in `SIGSTOP` la ConfigureRequest resta in coda e
    `windowsize --sync` non ottiene mai la sua conferma. Prima questo diventava
    un `TimeoutExpired` a meta' fixture; ora e' un `None`, e la geometria si
    legge lo stesso perche' quella non passa dal window manager.
    """
    require_wm_tools()
    pids = _openbox_pids()
    if not pids:
        pytest.skip("nessun processo openbox da fermare su questo display")

    for pid in pids:
        os.kill(pid, signal.SIGSTOP)
    try:
        time.sleep(0.3)
        assert xdo(
            ["windowsize", "--sync", str(probe_window.hwnd), "900", "700"], timeout=2.0
        ) is None, "con il WM fermo il --sync non puo' aver ricevuto conferma"
        # E la lettura continua a funzionare: e' quella che la fixture usa per
        # sapere dove mirare, e non dipende dal window manager.
        rect = window_geometry(probe_window.hwnd)
        assert set(rect) == {"x", "y", "width", "height"}, rect
    finally:
        for pid in pids:
            os.kill(pid, signal.SIGCONT)
        # Il window manager deve tornare a rispondere per i test che seguono.
        deadline = time.time() + 10
        while time.time() < deadline:
            check = subprocess.run(  # noqa: S603
                ["wmctrl", "-m"], capture_output=True, text=True, timeout=5,
                check=False, env=x_env(),
            )
            if check.returncode == 0:
                break
            time.sleep(0.1)


# ---------------------------------------------------------------------------
# La fixture: quello che consegna e' misurato e utilizzabile
# ---------------------------------------------------------------------------
def test_the_recorder_window_can_contain_the_offsets_the_tests_aim_at(event_recorder):
    """`center` deve stare dentro `rect`, con margine per gli scostamenti usati.

    I test di input mirano a `center` e si spostano fino a `MAX_AIM_OFFSET` px:
    se la finestra fosse piu' piccola, l'evento finirebbe sulla root window e il
    recorder direbbe — correttamente — di non aver visto nulla, facendo passare
    per un difetto di consegna quello che e' un difetto di mira.
    """
    rect = event_recorder.rect
    assert min(rect["width"], rect["height"]) >= MIN_RECORDER_SIDE, rect
    cx, cy = event_recorder.center
    assert rect["x"] + MAX_AIM_OFFSET <= cx <= rect["x"] + rect["width"] - MAX_AIM_OFFSET
    assert rect["y"] + MAX_AIM_OFFSET <= cy <= rect["y"] + rect["height"] - MAX_AIM_OFFSET


def test_the_recorder_holds_the_input_focus(event_recorder):
    """Senza fuoco il recorder non riceve tasti, e il test accuserebbe l'input."""
    assert wait_for_focus(event_recorder.hwnd, timeout=5.0) is True
