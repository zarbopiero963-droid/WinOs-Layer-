"""Input primitives against real SendInput on windows-latest.

The Linux half proves *delivery* by reading events back out of `xev`. Windows
has no equivalent oracle available here — there is no `xev`, and building an
event hook inside the test process would be testing the hook. So this file
asserts what Windows genuinely reports:

* the pointer, which `GetCursorPos` reads back, so move and drag verify it;
* the SendInput return count, which says how many events the OS accepted —
  checked rather than assumed;
* the refusals, which happen before anything is sent at all.

What it deliberately does NOT claim is that a focused window acted on the
keystroke. That is the honest limit of testing input from the outside on this
platform, and it is stated rather than papered over with an assertion that
would pass regardless.
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
# The pointer — the one input effect Windows reports back
# ---------------------------------------------------------------------------
def test_mouse_move_lands_where_it_was_sent(backend):
    result = backend.mouse_move(400, 300)
    assert result["ok"] is True, result
    position = backend.pointer_position()
    assert position is not None
    # Windows applies its own DPI scaling to absolute coordinates, so this is
    # "close", not "equal" — the same reason the window geometry tests use a
    # tolerance rather than asserting the request back.
    assert abs(position["x"] - 400) <= 4, position
    assert abs(position["y"] - 300) <= 4, position


def test_drag_ends_with_the_pointer_near_the_target(backend):
    result = backend.mouse_drag(200, 200, 500, 400, steps=5)
    assert result["ok"] is True, result
    position = backend.pointer_position()
    assert abs(position["x"] - 500) <= 4, position
    assert abs(position["y"] - 400) <= 4, position


# ---------------------------------------------------------------------------
# SendInput accepted the events — checked against the count it returns
# ---------------------------------------------------------------------------
def test_double_click_sends_four_events_in_one_call(backend):
    """Two down/up pairs, in a single SendInput.

    Two separate calls can fall outside GetDoubleClickTime, and then the
    application sees two single clicks — a different gesture from the one asked
    for. `ok` here means SendInput accepted all four.
    """
    result = backend.double_click(400, 300)
    assert result["ok"] is True, result
    assert result["clicks"] == 2
    assert result["method"] == "SendInput"


@pytest.mark.parametrize("direction", ["up", "down", "left", "right"])
def test_scroll_is_accepted_in_every_direction(backend, direction):
    result = backend.scroll(direction, 3)
    assert result["ok"] is True, result
    # Unlike X11, where a notch is a button press, Windows carries a signed
    # delta: WHEEL_DELTA (120) per notch, negative for down and left.
    expected = 360 if direction in ("up", "right") else -360
    assert result["delta"] == expected, result


def test_key_down_and_key_up_are_two_separate_events(backend):
    down = backend.key_down("shift")
    assert down["ok"] is True, down
    assert down["state"] == "down"
    up = backend.key_up("shift")
    assert up["ok"] is True, up
    assert up["state"] == "up"


def test_hotkey_is_accepted_and_releases_what_it_pressed(backend):
    result = backend.hotkey(["ctrl", "a"])
    assert result["ok"] is True, result
    assert result["chord"] == "ctrl+a"
    # A modifier left stuck down turns every later keystroke into a shortcut,
    # so the release is in a `finally`. If ctrl were still held, this ordinary
    # key press would be Ctrl+B instead of b.
    assert backend.key_press("b")["ok"] is True


# ---------------------------------------------------------------------------
# Refusals — before anything is sent
# ---------------------------------------------------------------------------
def test_an_unknown_button_is_refused_not_clicked_as_left(backend):
    """The defect this replaces, which was on BOTH backends.

    `.get(button, (LEFTDOWN, LEFTUP))` meant a typo produced a left click
    reported as the button the caller named.
    """
    for result in (
        backend.mouse_click(400, 300, "rihgt"),
        backend.double_click(400, 300, "rihgt"),
        backend.mouse_drag(200, 200, 300, 300, "rihgt"),
    ):
        assert result["ok"] is False, result
        assert "unknown mouse button" in result["error"], result


@pytest.mark.parametrize("direction,amount", [("diagonal", 3), ("down", 0), ("down", 101)])
def test_scroll_refuses_impossible_requests(backend, direction, amount):
    result = backend.scroll(direction, amount)
    assert result["ok"] is False, result


@pytest.mark.parametrize("key", ["", "   ", "a b", "x" * 40])
def test_key_down_refuses_a_name_that_cannot_be_a_key(backend, key):
    assert backend.key_down(key)["ok"] is False


def test_key_down_refuses_an_unknown_virtual_key(backend):
    """Deliberately stricter than key_press.

    key_press falls back to sending a unicode character for an unmapped name.
    A HELD key cannot: there would be no virtual key for key_up to release.
    """
    result = backend.key_down("NotAKeyAtAll")
    assert result["ok"] is False, result
    assert "unknown key" in result["error"], result


@pytest.mark.parametrize("keys", ["ctrl+a", [], ["a"] * 20, ["a", ""]])
def test_hotkey_refuses_a_malformed_chord(backend, keys):
    assert backend.hotkey(keys)["ok"] is False


@pytest.mark.parametrize("steps", [0, 500, "many"])
def test_drag_refuses_an_impossible_step_count(backend, steps):
    result = backend.mouse_drag(200, 200, 300, 300, steps=steps)
    assert result["ok"] is False, result
