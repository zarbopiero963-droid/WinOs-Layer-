"""move/resize/minimize/maximize against a REAL window on windows-latest.

The Linux half of this feature is covered in
tests/linux/test_window_manager_linux.py against a real X window. This is the
same contract exercised through `WindowsBackend`, on a real HWND, with the
result read back from `GetWindowRect` / `IsIconic` / `IsZoomed` rather than
inferred from a call that returned without raising.

Teardown closes the window AND kills the process tree. Launching `notepad.exe`
on Windows 11 can leave the app running under a different pid than the one
`Popen` returns — the same shape as the orphan the artifact smoke found in #11.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.windows

if sys.platform != "win32":
    pytest.skip("requires real Windows", allow_module_level=True)

from windows_os_api.backends.windows import WindowsBackend  # noqa: E402


@pytest.fixture
def backend(tmp_path):
    return WindowsBackend(str(tmp_path))


def _notepad_hwnds(backend: WindowsBackend) -> set[int]:
    return {
        w["hwnd"] for w in backend.list_windows() if "notepad" in (w.get("title") or "").lower()
    }


@pytest.fixture
def hwnd(backend):
    """A real top-level window, torn down whatever the test does to it."""
    before = _notepad_hwnds(backend)
    proc = subprocess.Popen(["notepad.exe"])  # noqa: S603

    found = None
    for _ in range(60):
        new = _notepad_hwnds(backend) - before
        if new:
            found = sorted(new)[0]
            break
        time.sleep(0.25)

    if found is None:
        _kill_tree(proc.pid)
        message = "notepad window never appeared (no interactive desktop?)"
        # In CI the runner has a desktop session, and a skip there would retire
        # the only real Windows coverage this feature has. WINOS_REQUIRE_WM
        # turns the skip into a failure exactly where coverage is claimed.
        if os.environ.get("WINOS_REQUIRE_WM") == "1":
            pytest.fail(message + " (WINOS_REQUIRE_WM=1)")
        pytest.skip(message)

    time.sleep(0.5)
    yield found

    backend.restore_window(found)
    backend.close_window(found)
    time.sleep(0.5)
    _kill_tree(proc.pid)


def _kill_tree(pid: int) -> None:
    subprocess.run(  # noqa: S603
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
        timeout=20,
        check=False,
    )


def _settle(seconds: float = 0.5) -> None:
    time.sleep(seconds)


# ---------------------------------------------------------------------------
# move / resize
# ---------------------------------------------------------------------------
def test_move_reports_where_the_window_actually_landed(backend, hwnd):
    before = backend.window_geometry(hwnd)
    assert before is not None

    result = backend.move_window(hwnd, 300, 200)
    assert result["ok"] is True, result
    assert result["requested"] == {"x": 300, "y": 200}

    _settle()
    observed = backend.window_geometry(hwnd)
    assert observed is not None
    # Approximate, not exact: Windows applies its own DPI/frame adjustment, and
    # asserting equality would be asserting something false about the platform.
    assert abs(observed["x"] - 300) <= 64, observed
    assert abs(observed["y"] - 200) <= 64, observed
    assert (observed["x"], observed["y"]) != (before["x"], before["y"]), (before, observed)


def test_move_returns_the_geometry_it_read_not_the_request(backend, hwnd):
    result = backend.move_window(hwnd, 400, 300)
    assert result["ok"] is True, result
    assert set(result["geometry"]) == {"x", "y", "width", "height"}
    _settle()
    assert result["geometry"] == backend.window_geometry(hwnd)


def test_resize_changes_the_window_and_reports_the_real_size(backend, hwnd):
    before = backend.window_geometry(hwnd)
    result = backend.resize_window(hwnd, 700, 500)
    assert result["ok"] is True, result

    _settle()
    observed = backend.window_geometry(hwnd)
    assert (observed["width"], observed["height"]) != (before["width"], before["height"])
    # Windows enforces the window's own minimum tracking size, so the result can
    # be larger than asked. Close, not equal.
    assert abs(observed["width"] - 700) <= 64, observed
    assert abs(observed["height"] - 500) <= 64, observed


def test_move_refuses_a_handle_that_is_not_a_window(backend):
    result = backend.move_window(99999999, 10, 10)
    assert result["ok"] is False
    assert "not found" in result["error"]


@pytest.mark.parametrize("width,height", [(0, 100), (100, 0), (-5, 100), (99999, 100)])
def test_resize_refuses_impossible_sizes(backend, hwnd, width, height):
    before = backend.window_geometry(hwnd)
    result = backend.resize_window(hwnd, width, height)
    assert result["ok"] is False, result
    assert "out of range" in result["error"]
    _settle(0.2)
    # Refused means untouched, not clamped to something arbitrary.
    assert backend.window_geometry(hwnd) == before


def test_move_refuses_out_of_range_coordinates(backend, hwnd):
    result = backend.move_window(hwnd, 99999, 0)
    assert result["ok"] is False
    assert "out of range" in result["error"]


# ---------------------------------------------------------------------------
# minimize / maximize / restore
# ---------------------------------------------------------------------------
def test_minimize_is_confirmed_by_the_os(backend, hwnd):
    result = backend.minimize_window(hwnd)
    assert result["ok"] is True, result
    assert result["state"] == "minimized", result
    assert result["verified"] is True, result


def test_maximize_is_confirmed_and_the_window_grows(backend, hwnd):
    backend.resize_window(hwnd, 400, 300)
    _settle()
    before = backend.window_geometry(hwnd)

    result = backend.maximize_window(hwnd)
    assert result["ok"] is True, result
    assert result["state"] == "maximized", result

    _settle()
    observed = backend.window_geometry(hwnd)
    assert observed["width"] > before["width"], (before, observed)


def test_restore_undoes_maximize(backend, hwnd):
    backend.maximize_window(hwnd)
    _settle()
    result = backend.restore_window(hwnd)
    assert result["ok"] is True, result
    assert result["state"] == "normal", result


def test_restore_undoes_minimize(backend, hwnd):
    backend.minimize_window(hwnd)
    _settle()
    result = backend.restore_window(hwnd)
    assert result["ok"] is True, result
    assert result["state"] == "normal", result


def test_maximize_refuses_a_handle_that_is_not_a_window(backend):
    result = backend.maximize_window(99999999)
    assert result["ok"] is False, result
    assert "not found" in result["error"], result
