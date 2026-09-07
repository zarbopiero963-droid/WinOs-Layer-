"""move/resize/minimize/maximize against a REAL X window under Xvfb.

Not a fixture, not a mock: the fixture starts a window manager and a real
`xterm` on the live display, and every assertion reads the resulting geometry
or `_NET_WM_STATE` back from the X server.

Two things this suite is built around, both measured before it was written
(see the PR body for the probe output):

* **The request is not the result.** A move to (300, 200) landed at (302, 240)
  — openbox's frame offset — and a resize to 700x500 came back 700x498,
  because xterm snaps to character cells. Any test asserting exact equality
  would be asserting something false about every real window manager.
* **Exit codes lie here.** `wmctrl -i -r 99999999 -b add,maximized_vert` exits
  **0** for a window id that does not exist. That is why the backend verifies
  the effect, and why `test_maximize_refuses_a_window_that_does_not_exist`
  exists at all — it is the regression guard for trusting that exit code.
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


def _settle(seconds: float = 0.6) -> None:
    """X requests are asynchronous; the WM answers on its own schedule."""
    time.sleep(seconds)


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------
def test_move_reports_where_the_window_actually_landed(linux_backend, hwnd):
    before = linux_backend.window_geometry(hwnd)
    assert before is not None

    result = linux_backend.move_window(hwnd, 300, 200)
    assert result["ok"] is True, result
    assert result["requested"] == {"x": 300, "y": 200}

    _settle()
    observed = linux_backend.window_geometry(hwnd)
    assert observed is not None
    # The window moved, and it moved to roughly where it was asked to go. The
    # slack is the WM frame — exact equality is the assertion that would be
    # wrong here, not the one that would be strict.
    assert abs(observed["x"] - 300) <= 64, observed
    assert abs(observed["y"] - 200) <= 64, observed
    assert (observed["x"], observed["y"]) != (before["x"], before["y"]), observed


def test_move_returns_the_geometry_it_read_not_the_request(linux_backend, hwnd):
    """`requested` and `geometry` are separate fields, and both are populated."""
    result = linux_backend.move_window(hwnd, 400, 300)
    assert result["ok"] is True, result
    assert set(result["geometry"]) == {"x", "y", "width", "height"}
    _settle()
    # `geometry` came from the X server, so it agrees with a fresh read.
    assert result["geometry"] == linux_backend.window_geometry(hwnd)


def test_move_refuses_a_window_that_does_not_exist(linux_backend):
    result = linux_backend.move_window(99999999, 10, 10)
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_move_refuses_out_of_range_coordinates(linux_backend, hwnd):
    result = linux_backend.move_window(hwnd, 99999, 0)
    assert result["ok"] is False
    assert "out of range" in result["error"]


# ---------------------------------------------------------------------------
# resize
# ---------------------------------------------------------------------------
def test_resize_changes_the_window_and_reports_the_real_size(linux_backend, hwnd):
    before = linux_backend.window_geometry(hwnd)
    result = linux_backend.resize_window(hwnd, 700, 500)
    assert result["ok"] is True, result

    _settle()
    observed = linux_backend.window_geometry(hwnd)
    assert observed["width"] != before["width"] or observed["height"] != before["height"]
    # xterm quantises to character cells — 700x500 came back 700x498 when this
    # was measured. Close, not equal.
    assert abs(observed["width"] - 700) <= 32, observed
    assert abs(observed["height"] - 500) <= 32, observed


@pytest.mark.parametrize("width,height", [(0, 100), (100, 0), (-5, 100), (99999, 100)])
def test_resize_refuses_impossible_sizes(linux_backend, hwnd, width, height):
    before = linux_backend.window_geometry(hwnd)
    result = linux_backend.resize_window(hwnd, width, height)
    assert result["ok"] is False, result
    assert "out of range" in result["error"]
    _settle(0.2)
    # Refused means untouched, not clamped to something arbitrary.
    assert linux_backend.window_geometry(hwnd) == before


# ---------------------------------------------------------------------------
# minimize / maximize / restore
# ---------------------------------------------------------------------------
def test_minimize_is_confirmed_by_the_window_manager(linux_backend, hwnd):
    result = linux_backend.minimize_window(hwnd)
    assert result["ok"] is True, result
    assert result["verified"] is True, result
    assert result["state"] == "minimized", result
    assert any("HIDDEN" in atom for atom in result["wm_state"]), result


def test_maximize_is_confirmed_and_the_window_grows(linux_backend, hwnd):
    linux_backend.resize_window(hwnd, 400, 300)
    _settle()
    before = linux_backend.window_geometry(hwnd)

    result = linux_backend.maximize_window(hwnd)
    assert result["ok"] is True, result
    assert result["verified"] is True, result
    assert result["state"] == "maximized", result

    _settle()
    observed = linux_backend.window_geometry(hwnd)
    assert observed["width"] > before["width"], (before, observed)
    # NOT "equals the screen": openbox reserved 38px of the 1024-high screen
    # when this was measured. Bigger is the claim that holds.


def test_restore_undoes_maximize(linux_backend, hwnd):
    linux_backend.maximize_window(hwnd)
    _settle()
    result = linux_backend.restore_window(hwnd)
    assert result["ok"] is True, result
    assert result["state"] == "normal", result
    assert not any("MAXIMIZED" in atom for atom in result.get("wm_state", [])), result


def test_restore_maps_a_minimized_window_back(linux_backend, hwnd):
    """The reason restore runs two tools instead of one.

    wmctrl clears the maximize atoms but will not re-map a minimized window;
    dropping xdotool from restore would leave this reporting "normal" while the
    window stayed unmapped.
    """
    linux_backend.minimize_window(hwnd)
    _settle()
    result = linux_backend.restore_window(hwnd)
    assert result["ok"] is True, result
    assert result["state"] == "normal", result

    _settle()
    mapped = subprocess.run(  # noqa: S603
        ["xwininfo", "-id", str(hwnd)], capture_output=True, text=True, timeout=5,
        check=False, env=x_env(),
    )
    if mapped.returncode == 0 and "Map State" in mapped.stdout:
        assert "IsUnMapped" not in mapped.stdout, mapped.stdout


def test_maximize_refuses_a_window_that_does_not_exist(linux_backend):
    """The regression guard for the exit code that lies.

    `wmctrl -i -r 99999999 -b add,maximized_vert,maximized_horz` exits 0. A
    backend that reported success from that return code would pass this call
    and claim it maximized a window that was never there.
    """
    result = linux_backend.maximize_window(99999999)
    assert result["ok"] is False, result
    assert "not found" in result["error"], result


def test_minimize_refuses_a_window_that_does_not_exist(linux_backend):
    result = linux_backend.minimize_window(99999999)
    assert result["ok"] is False
    assert "not found" in result["error"]
