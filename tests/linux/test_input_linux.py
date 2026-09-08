"""Input primitives verified by what the X server ACTUALLY delivered.

A keystroke has no readback the way window geometry does: once the event is
handed to the X server it belongs to whatever window has focus, and nothing
reports what that window did with it. So `ok` from the backend means the narrow,
true thing — the event was accepted — and *delivery* is proven here, by reading
it back out of `xev`, which prints every event it receives.

That distinction is the point of this file. A test that only asserted
`result["ok"] is True` would pass against a backend that ran nothing at all.

Measured before these tests were written (see the PR body):

    click            -> ButtonPress, button 1
    double_click     -> button 1 three times (one single + two of the double)
    scroll up/down   -> button 4 / button 5   (a wheel notch IS a button on X11)
    key_down/key_up  -> Shift_L twice: one KeyPress, one KeyRelease
    hotkey ctrl+a    -> Control_L x2 + a x2
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.linux

if sys.platform == "win32":
    pytest.skip("Linux only", allow_module_level=True)


# ---------------------------------------------------------------------------
# The pointer — the one input effect X will report back
# ---------------------------------------------------------------------------
def test_mouse_move_reports_where_the_pointer_actually_went(linux_backend, window_manager):
    result = linux_backend.mouse_move(400, 300)
    assert result["ok"] is True, result
    assert result["position"] == {"x": 400, "y": 300}, result
    assert linux_backend.pointer_position() == {"x": 400, "y": 300}


def test_mouse_move_refuses_out_of_range_coordinates(linux_backend, window_manager):
    result = linux_backend.mouse_move(99999, 0)
    assert result["ok"] is False
    assert "out of range" in result["error"]


# ---------------------------------------------------------------------------
# Clicks — counted from what xev received
# ---------------------------------------------------------------------------
def test_double_click_delivers_two_presses_not_one(linux_backend, event_recorder):
    cx, cy = event_recorder.center
    result = linux_backend.double_click(cx, cy)
    assert result["ok"] is True, result
    assert result["clicks"] == 2

    # The claim is "two", so two is what is asserted — a single click reaching
    # the window would satisfy `ok` but not this.
    presses = event_recorder.wait_for(r"^ButtonPress", at_least=2)
    assert presses >= 2, event_recorder.text()[:2000]
    assert event_recorder.count(r"button 1\b") >= 4  # 2 presses + 2 releases


def test_double_click_with_the_right_button_delivers_button_3(linux_backend, event_recorder):
    cx, cy = event_recorder.center
    result = linux_backend.double_click(cx, cy, "right")
    assert result["ok"] is True, result
    event_recorder.wait_for(r"^ButtonPress", at_least=2)
    assert event_recorder.count(r"button 3\b") >= 4, event_recorder.text()[:2000]
    # And the button the caller did NOT ask for was not sent.
    assert event_recorder.count(r"button 1\b") == 0, event_recorder.text()[:2000]


def test_a_typo_in_the_button_name_sends_nothing(linux_backend, event_recorder):
    """The defect this replaces: an unknown button used to click LEFT.

    `.get(button, "1")` meant `double_click(x, y, "rihgt")` performed a left
    click and reported `{"ok": True, "button": "rihgt"}` — the name asked for,
    next to an action that was something else.
    """
    cx, cy = event_recorder.center
    result = linux_backend.double_click(cx, cy, "rihgt")
    assert result["ok"] is False, result
    assert "unknown mouse button" in result["error"]

    # Nothing was sent — not "something was sent and we called it a failure".
    assert event_recorder.count(r"^ButtonPress") == 0, event_recorder.text()[:2000]


# ---------------------------------------------------------------------------
# Scroll — a wheel notch is a button press on X11
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("direction,button", [("up", 4), ("down", 5),
                                              ("left", 6), ("right", 7)])
def test_scroll_delivers_the_right_wheel_button(linux_backend, event_recorder,
                                                direction, button):
    linux_backend.mouse_move(*event_recorder.center)
    result = linux_backend.scroll(direction, 3)
    assert result["ok"] is True, result
    assert result["button"] == button

    event_recorder.wait_for(rf"button {button}\b", at_least=3)
    assert event_recorder.count(rf"button {button}\b") >= 3, event_recorder.text()[:2000]


def test_scroll_amount_is_the_number_of_notches_delivered(linux_backend, event_recorder):
    linux_backend.mouse_move(*event_recorder.center)
    linux_backend.scroll("down", 5)
    event_recorder.wait_for(r"^ButtonPress", at_least=5)
    assert event_recorder.count(r"^ButtonPress") == 5, event_recorder.text()[:3000]


@pytest.mark.parametrize("direction,amount", [("diagonal", 3), ("down", 0),
                                              ("down", 101), ("down", -1)])
def test_scroll_refuses_impossible_requests(linux_backend, event_recorder,
                                            direction, amount):
    result = linux_backend.scroll(direction, amount)
    assert result["ok"] is False, result
    assert event_recorder.count(r"^ButtonPress") == 0, event_recorder.text()[:2000]


# ---------------------------------------------------------------------------
# key_down / key_up — a HELD key, which is the whole point of splitting them
# ---------------------------------------------------------------------------
def test_key_down_and_key_up_are_two_separate_events(linux_backend, event_recorder):
    """Held, then released — not a press.

    If key_down were implemented as a full press (down+up), the KeyRelease would
    arrive before key_up was ever called, and holding a modifier across other
    input would be impossible.
    """
    down = linux_backend.key_down("shift")
    assert down["ok"] is True, down
    assert down["state"] == "down"

    presses = event_recorder.wait_for(r"^KeyPress", at_least=1)
    assert presses == 1, event_recorder.text()[:2000]
    # The key is still held: nothing has released it.
    assert event_recorder.count(r"^KeyRelease") == 0, event_recorder.text()[:2000]

    up = linux_backend.key_up("shift")
    assert up["ok"] is True, up
    assert up["state"] == "up"
    assert event_recorder.wait_for(r"^KeyRelease", at_least=1) == 1


def test_a_held_modifier_changes_the_key_pressed_while_it_is_down(linux_backend,
                                                                  event_recorder):
    """What holding a key is FOR — and the assertion that proves it.

    Shift held, then 'a'. The X server does not deliver a plain 'a' with a note
    that shift happened; it delivers a different keysym entirely:

        keysym 0x61, a   without shift
        keysym 0x41, A   with shift held      <- measured

    So asserting that BOTH events arrived would prove nothing about the hold —
    two unrelated keystrokes would satisfy it. Asserting that the second one
    came through as capital A, and that lowercase a never appears, is the claim
    that only holds if key_down really left the modifier down.
    """
    linux_backend.key_down("shift")
    assert event_recorder.wait_for(r"Shift_L", at_least=1) >= 1

    linux_backend.key_press("a")
    assert event_recorder.wait_for(r"keysym 0x41, A", at_least=1) >= 1, \
        event_recorder.text()[:3000]

    linux_backend.key_up("shift")
    event_recorder.wait_for(r"^KeyRelease", at_least=2)

    text = event_recorder.text()
    assert "keysym 0x41, A" in text, text[:3000]
    assert "keysym 0x61, a" not in text, text[:3000]


@pytest.mark.parametrize("key", ["", "   ", "a b", "x" * 40])
def test_key_down_refuses_a_name_that_cannot_be_a_key(linux_backend, event_recorder, key):
    result = linux_backend.key_down(key)
    assert result["ok"] is False, result
    assert event_recorder.count(r"^KeyPress") == 0, event_recorder.text()[:2000]


def test_an_unknown_keysym_reports_success_but_delivers_nothing(linux_backend,
                                                                event_recorder):
    """A limit of the tool, recorded rather than papered over.

    The first version of this test asserted that `key_down("NotAKeysymAtAll")`
    is refused, on the assumption that xdotool reports an unknown keysym. It
    does not — measured: `xdotool keydown NotAKeysymAtAll` exits **0**. So the
    backend cannot detect it either, and claiming otherwise would have been a
    test asserting something untrue about the platform.

    What IS true, and worth pinning, is that nothing reaches the window. If a
    later change starts delivering a stray keystroke for an unknown name, this
    catches it.
    """
    result = linux_backend.key_down("NotAKeysymAtAll")
    assert result["ok"] is True, result  # xdotool exits 0; the backend reports what it sees
    assert event_recorder.wait_for(r"^KeyPress", at_least=1, timeout=1.0) == 0, \
        event_recorder.text()[:2000]


# ---------------------------------------------------------------------------
# hotkey — a chord, delivered as one
# ---------------------------------------------------------------------------
def test_hotkey_delivers_every_key_in_the_chord(linux_backend, event_recorder):
    result = linux_backend.hotkey(["ctrl", "a"])
    assert result["ok"] is True, result
    assert result["chord"] == "ctrl+a"

    event_recorder.wait_for(r"^KeyRelease", at_least=2)
    text = event_recorder.text()
    assert "Control_L" in text, text[:2000]
    assert "keysym 0x61, a" in text, text[:2000]
    # Both went down and both came back up: a chord that stayed held would
    # leave the keyboard modified for everything after it.
    assert event_recorder.count(r"^KeyPress") >= 2, text[:2000]
    assert event_recorder.count(r"^KeyRelease") >= 2, text[:2000]


@pytest.mark.parametrize("keys", ["ctrl+a", [], ["a"] * 20, ["a", ""], 42])
def test_hotkey_refuses_a_malformed_chord(linux_backend, event_recorder, keys):
    result = linux_backend.hotkey(keys)
    assert result["ok"] is False, result
    assert event_recorder.count(r"^KeyPress") == 0, event_recorder.text()[:2000]


# ---------------------------------------------------------------------------
# mouse_drag — parity with WindowsBackend, which had it and Linux did not
# ---------------------------------------------------------------------------
def test_drag_presses_moves_and_releases(linux_backend, event_recorder):
    cx, cy = event_recorder.center
    result = linux_backend.mouse_drag(cx - 60, cy - 60, cx + 60, cy + 40, steps=5)
    assert result["ok"] is True, result
    assert result["position"] == {"x": cx + 60, "y": cy + 40}, result

    event_recorder.wait_for(r"^ButtonRelease", at_least=1)
    text = event_recorder.text()
    assert event_recorder.count(r"^ButtonPress") >= 1, text[:2000]
    assert event_recorder.count(r"^ButtonRelease") >= 1, text[:2000]
    # The moves in between are what makes it a drag rather than a teleport.
    assert event_recorder.count(r"^MotionNotify") >= 2, text[:3000]


def test_drag_ends_with_the_pointer_where_it_was_sent(linux_backend, window_manager):
    result = linux_backend.mouse_drag(100, 100, 450, 380)
    assert result["ok"] is True, result
    assert linux_backend.pointer_position() == {"x": 450, "y": 380}


def test_drag_does_not_leave_the_button_held(linux_backend, event_recorder):
    """A button left down is a mouse the user cannot use.

    Presses and releases must balance — the release is in a `finally` for
    exactly this reason.
    """
    cx, cy = event_recorder.center
    linux_backend.mouse_drag(cx - 40, cy - 40, cx + 40, cy + 40, steps=3)
    event_recorder.wait_for(r"^ButtonRelease", at_least=1)
    assert event_recorder.count(r"^ButtonPress") == event_recorder.count(r"^ButtonRelease"), \
        event_recorder.text()[:3000]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"button": "rihgt"},
        {"steps": 0},
        {"steps": 500},
        {"steps": "many"},
    ],
)
def test_drag_refuses_a_malformed_request(linux_backend, event_recorder, kwargs):
    cx, cy = event_recorder.center
    result = linux_backend.mouse_drag(cx - 40, cy - 40, cx + 40, cy + 40, **kwargs)
    assert result["ok"] is False, result
    assert event_recorder.count(r"^ButtonPress") == 0, event_recorder.text()[:2000]
