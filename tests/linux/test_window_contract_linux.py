"""focus_window / close_window: `ok` must mean the thing actually happened.

The contract these tests pin, decided by the owner in issue #6:

    finestra inesistente          finestra inesistente
            ↓                             ↓
      focus_window()      =>        focus_window()
            ↓                             ↓
        ok: true   ❌            ok: false, WINDOW_NOT_FOUND

Both methods used to run their tool with `check=False` and return
`{"ok": True}` whatever happened. That was not merely optimistic — it was
measurably wrong, and the measurement is the reason these tests exist:

    window whose process was killed:
        xdotool windowclose   -> exit 1
        wmctrl -i -c          -> exit 0     <- reports success on a dead window

`wmctrl` lying with exit 0 is the same behaviour that #18 found in
`wmctrl -b add,maximized_vert`. An exit code cannot carry this contract, so
both methods read the effect back instead.
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from tests.linux.conftest import x_env

pytestmark = pytest.mark.linux

if sys.platform == "win32":
    pytest.skip("Linux only", allow_module_level=True)

GHOST = 99999999  # never a window


# ---------------------------------------------------------------------------
# focus_window
# ---------------------------------------------------------------------------
def test_focus_reports_the_window_that_actually_has_it(linux_backend, probe_window):
    result = linux_backend.focus_window(probe_window.hwnd)
    assert result["ok"] is True, result
    assert result["verified"] is True
    assert result["active_window"] == probe_window.hwnd, result
    # And the OS agrees with the answer that was reported.
    assert linux_backend.active_window() == probe_window.hwnd


def test_focus_moves_between_two_real_windows(linux_backend, probe_window,
                                              second_probe_window):
    """Two windows, so "it has focus" is a claim that can be wrong.

    With one window on the display, any implementation looks correct — the
    window it names is the only candidate.
    """
    first = linux_backend.focus_window(probe_window.hwnd)
    assert first["ok"] is True, first
    assert linux_backend.active_window() == probe_window.hwnd

    second = linux_backend.focus_window(second_probe_window.hwnd)
    assert second["ok"] is True, second
    assert second["active_window"] == second_probe_window.hwnd
    assert linux_backend.active_window() == second_probe_window.hwnd
    assert linux_backend.active_window() != probe_window.hwnd


def test_focus_on_a_window_that_does_not_exist(linux_backend, window_manager):
    result = linux_backend.focus_window(GHOST)
    assert result["ok"] is False, result
    assert result["error_code"] == "WINDOW_NOT_FOUND", result
    assert str(GHOST) in result["error"]


@pytest.mark.parametrize("hwnd", [0, -1, 2**31])
def test_focus_on_an_invalid_handle(linux_backend, window_manager, hwnd):
    """Not a window id at all — refused the same way as one that is simply gone."""
    result = linux_backend.focus_window(hwnd)
    assert result["ok"] is False, result
    assert result["error_code"] == "WINDOW_NOT_FOUND", result


# ---------------------------------------------------------------------------
# close_window
# ---------------------------------------------------------------------------
def test_close_waits_until_the_window_is_really_gone(linux_backend, probe_window):
    result = linux_backend.close_window(probe_window.hwnd)
    assert result["ok"] is True, result
    assert result["closed"] is True
    assert result["verified"] is True
    # `ok` claimed the window is gone, so it must be gone.
    assert linux_backend.window_geometry(probe_window.hwnd) is None


def test_closing_the_same_window_twice_is_not_a_second_success(linux_backend,
                                                               probe_window):
    assert linux_backend.close_window(probe_window.hwnd)["ok"] is True
    again = linux_backend.close_window(probe_window.hwnd)
    assert again["ok"] is False, again
    assert again["error_code"] == "WINDOW_NOT_FOUND", again


def test_close_on_a_window_that_does_not_exist(linux_backend, window_manager):
    """The case where `wmctrl -i -c` exits 0 and means nothing.

    Measured: on a handle that names no window, `wmctrl -i -c` returns 0. An
    implementation trusting that exit code reports a close that never happened.
    """
    result = linux_backend.close_window(GHOST)
    assert result["ok"] is False, result
    assert result["error_code"] == "WINDOW_NOT_FOUND", result


def test_a_window_whose_process_died_during_the_operation(linux_backend,
                                                          window_manager):
    """The owner's case: the window goes away underneath the call.

    Its process is killed between the handle being obtained and the operation
    being attempted. Both methods must report WINDOW_NOT_FOUND — this is
    exactly where `wmctrl -i -c` returns 0 and the old code said "ok".
    """
    title = f"winos-doomed-{int(time.time() * 1000) % 100000}"
    proc = subprocess.Popen(  # noqa: S603
        ["xterm", "-title", title, "-e", "sleep", "300"],
        env=x_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    hwnd = None
    for _ in range(75):
        r = subprocess.run(  # noqa: S603
            ["xdotool", "search", "--name", title],
            capture_output=True, text=True, timeout=5, check=False, env=x_env(),
        )
        ids = [line for line in r.stdout.split() if line.isdigit()]
        if ids:
            hwnd = int(ids[0])
            break
        time.sleep(0.2)
    assert hwnd is not None, "the doomed window never appeared"

    proc.kill()
    proc.wait(timeout=10)
    for _ in range(100):  # wait for X to reap the window, not just the process
        if linux_backend.window_geometry(hwnd) is None:
            break
        time.sleep(0.05)

    closed = linux_backend.close_window(hwnd)
    assert closed["ok"] is False, closed
    assert closed["error_code"] == "WINDOW_NOT_FOUND", closed

    focused = linux_backend.focus_window(hwnd)
    assert focused["ok"] is False, focused
    assert focused["error_code"] == "WINDOW_NOT_FOUND", focused


def test_close_reports_a_window_that_refuses_to_go(linux_backend, probe_window,
                                                   monkeypatch):
    """The WINDOW_STILL_OPEN path, driven rather than staged.

    Nothing available here ignores WM_DELETE_WINDOW — xterm closes, and so does
    xev, which was the obvious candidate. So the condition is produced by making
    the window appear to persist, which exercises the real polling-and-timeout
    code rather than a stub of it.

    An unsaved document is the everyday version of this, and reporting it as a
    successful close is the whole defect being fixed.
    """
    geometry = linux_backend.window_geometry(probe_window.hwnd)
    assert geometry is not None
    monkeypatch.setattr(linux_backend, "window_geometry", lambda hwnd: geometry)

    started = time.time()
    result = linux_backend.close_window(probe_window.hwnd, timeout=0.4)
    elapsed = time.time() - started

    assert result["ok"] is False, result
    assert result["error_code"] == "WINDOW_STILL_OPEN", result
    assert result["closed"] is False
    assert "still open" in result["error"]
    # It waited rather than giving up at once, and did not wait forever.
    assert 0.3 <= elapsed <= 5, elapsed


def test_the_tools_being_absent_is_its_own_answer(linux_backend, monkeypatch):
    """No tool is a different outcome from "the window would not close".

    An operator fixes the first in their package list and the second in their
    application; reporting one as the other sends them to the wrong place.
    """
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    for result in (linux_backend.focus_window(1), linux_backend.close_window(1)):
        assert result["ok"] is False, result
        assert result["error_code"] == "TOOL_UNAVAILABLE", result
