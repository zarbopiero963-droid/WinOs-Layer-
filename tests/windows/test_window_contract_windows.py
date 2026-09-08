"""focus_window / close_window against real HWNDs on windows-latest.

The Windows half of the contract decided in issue #6. The defect is the same as
on Linux, but the mechanism differs and that is the point of testing it here:

* `SetForegroundWindow` is a call Windows is entitled to REFUSE. A process that
  does not own the foreground cannot simply take it; the documented behaviour
  is to flash the taskbar button instead. So neither "it did not raise" nor its
  return value settles whether the window has focus — `GetForegroundWindow` does.

* `PostMessage(WM_CLOSE)` is **asynchronous**. It returns as soon as the message
  is queued, so the old `{"ok": True}` meant no more than "the message was
  posted". An application with an unsaved document puts up "save changes?" and
  stays open, and the old answer said it had closed.
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.windows

if sys.platform != "win32":
    pytest.skip("requires real Windows", allow_module_level=True)

from windows_os_api.backends.windows import WindowsBackend  # noqa: E402

GHOST = 99999999


@pytest.fixture
def backend(tmp_path):
    return WindowsBackend(str(tmp_path))


def _notepad_hwnds(backend: WindowsBackend) -> set[int]:
    return {
        w["hwnd"] for w in backend.list_windows()
        if "notepad" in (w.get("title") or "").lower()
    }


def _open_notepad(backend: WindowsBackend):
    """Launch Notepad and return (hwnd, proc), or (None, proc) if it never shows."""
    before = _notepad_hwnds(backend)
    proc = subprocess.Popen(["notepad.exe"])  # noqa: S603
    for _ in range(60):
        new = _notepad_hwnds(backend) - before
        if new:
            time.sleep(0.5)
            return sorted(new)[0], proc
        time.sleep(0.25)
    return None, proc


def _kill_tree(pid: int) -> None:
    subprocess.run(  # noqa: S603
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True, timeout=20, check=False,
    )


@pytest.fixture
def window(backend):
    hwnd, proc = _open_notepad(backend)
    if hwnd is None:
        _kill_tree(proc.pid)
        pytest.fail("notepad window never appeared (no interactive desktop?)")
    yield hwnd
    backend.close_window(hwnd, timeout=2)
    _kill_tree(proc.pid)


# ---------------------------------------------------------------------------
# focus_window
# ---------------------------------------------------------------------------
def test_focus_reports_the_window_that_actually_has_it(backend, window):
    result = backend.focus_window(window)
    assert result["ok"] is True, result
    assert result["verified"] is True
    assert result["active_window"] == window, result
    assert backend.active_window() == window


def test_focus_on_a_handle_that_is_not_a_window(backend):
    result = backend.focus_window(GHOST)
    assert result["ok"] is False, result
    assert result["error_code"] == "WINDOW_NOT_FOUND", result


@pytest.mark.parametrize("hwnd", [0, -1])
def test_focus_on_an_invalid_handle(backend, hwnd):
    result = backend.focus_window(hwnd)
    assert result["ok"] is False, result
    assert result["error_code"] == "WINDOW_NOT_FOUND", result


# ---------------------------------------------------------------------------
# close_window
# ---------------------------------------------------------------------------
def test_close_waits_until_the_window_is_really_gone(backend):
    """`ok` claims the window is gone, so it has to be gone.

    PostMessage returns immediately; this is the assertion that separates
    "the message was queued" from "the window closed".
    """
    hwnd, proc = _open_notepad(backend)
    assert hwnd is not None
    try:
        result = backend.close_window(hwnd)
        assert result["ok"] is True, result
        assert result["closed"] is True
        assert result["verified"] is True
        assert backend.window_geometry(hwnd) is None
    finally:
        _kill_tree(proc.pid)


def test_closing_the_same_window_twice_is_not_a_second_success(backend):
    hwnd, proc = _open_notepad(backend)
    assert hwnd is not None
    try:
        assert backend.close_window(hwnd)["ok"] is True
        again = backend.close_window(hwnd)
        assert again["ok"] is False, again
        assert again["error_code"] == "WINDOW_NOT_FOUND", again
    finally:
        _kill_tree(proc.pid)


def test_close_on_a_handle_that_is_not_a_window(backend):
    result = backend.close_window(GHOST)
    assert result["ok"] is False, result
    assert result["error_code"] == "WINDOW_NOT_FOUND", result


def test_a_window_whose_process_died_during_the_operation(backend):
    """The owner's case: the window goes away underneath the call.

    Its process is killed after the handle is obtained, so the handle names
    nothing by the time the operation runs. Both methods must say so rather
    than reporting a success against a window that is not there.
    """
    hwnd, proc = _open_notepad(backend)
    assert hwnd is not None
    _kill_tree(proc.pid)
    for _ in range(100):
        if backend.window_geometry(hwnd) is None:
            break
        time.sleep(0.05)

    closed = backend.close_window(hwnd)
    assert closed["ok"] is False, closed
    assert closed["error_code"] == "WINDOW_NOT_FOUND", closed

    focused = backend.focus_window(hwnd)
    assert focused["ok"] is False, focused
    assert focused["error_code"] == "WINDOW_NOT_FOUND", focused


def test_close_reports_a_window_that_refuses_to_go(backend, window, monkeypatch):
    """The WINDOW_STILL_OPEN path — the "save changes?" case.

    Produced by making the window appear to persist, so the real
    polling-and-timeout code runs rather than a stub of it. Driving Notepad into
    a genuine unsaved-changes dialog would need UI automation inside a test that
    is about the close contract, not about typing.
    """
    geometry = backend.window_geometry(window)
    assert geometry is not None
    monkeypatch.setattr(backend, "window_geometry", lambda hwnd: geometry)

    started = time.time()
    result = backend.close_window(window, timeout=0.4)
    elapsed = time.time() - started

    assert result["ok"] is False, result
    assert result["error_code"] == "WINDOW_STILL_OPEN", result
    assert result["closed"] is False
    assert 0.3 <= elapsed <= 5, elapsed


def test_win32gui_being_unavailable_is_its_own_answer(backend, monkeypatch):
    monkeypatch.setattr(backend, "_win32gui", None)
    for result in (backend.focus_window(1), backend.close_window(1)):
        assert result["ok"] is False, result
        assert result["error_code"] == "TOOL_UNAVAILABLE", result
