"""The input contract, in the places every backend has to honour it.

Real delivery is covered where it is real — tests/linux against `xev` under
Xvfb, tests/windows against SendInput on the runner. This suite covers what
must hold identically everywhere and neither of those can check alone:

* the validator refuses the same requests on every platform, before anything
  is sent;
* the three backends implement the same surface, so a caller cannot get a
  different contract by reaching a different one.
"""
from __future__ import annotations

import pytest

from windows_os_api.backends.base import OSBackend
from windows_os_api.backends.fake import FakeBackend
from windows_os_api.os.input.validation import (
    InputRejected,
    validate_button,
    validate_hotkey,
    validate_key,
    validate_scroll,
    validate_steps,
)

INPUT_METHODS = (
    "double_click",
    "scroll",
    "key_down",
    "key_up",
    "hotkey",
    "mouse_drag",
    "pointer_position",
)


# ---------------------------------------------------------------------------
# The defect this validator exists for
# ---------------------------------------------------------------------------
def test_an_unknown_button_is_refused_not_defaulted_to_left():
    """The bug on main: `.get(button, "1")`.

    `mouse_click(x, y, "rihgt")` performed a LEFT click and returned
    `{"ok": True, "button": "rihgt"}` — the name the caller asked for, reported
    next to an action that was something else. Reporting an action you did not
    take is worse than refusing one you cannot.
    """
    with pytest.raises(InputRejected) as exc:
        validate_button("rihgt")
    assert "unknown mouse button" in str(exc.value)


@pytest.mark.parametrize("name", ["left", "middle", "right", "LEFT", "Right"])
def test_known_buttons_pass_and_are_normalised(name):
    assert validate_button(name) == name.lower()


@pytest.mark.parametrize("value", [1, None, True, ["left"]])
def test_a_button_must_be_a_string(value):
    with pytest.raises(InputRejected):
        validate_button(value)


# ---------------------------------------------------------------------------
# Scroll
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("direction", ["up", "down", "left", "right"])
def test_scroll_directions_pass(direction):
    assert validate_scroll(direction, 3) == (direction, 3)


@pytest.mark.parametrize(
    "direction,amount",
    [("diagonal", 3), ("", 3), ("down", 0), ("down", -1), ("down", 101),
     ("down", "3"), ("down", True), ("down", 1.5)],
)
def test_scroll_refuses_impossible_requests(direction, amount):
    with pytest.raises(InputRejected):
        validate_scroll(direction, amount)


def test_the_scroll_cap_is_a_cap_not_a_clamp():
    """Over the limit is refused, not quietly reduced.

    Scrolling 100 notches when 100000 were asked for would be doing something
    the caller did not request and calling it success.
    """
    with pytest.raises(InputRejected) as exc:
        validate_scroll("down", 100_000)
    assert "out of range" in str(exc.value)


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", ["a", "shift", "Return", "ctrl"])
def test_valid_key_names_pass(key):
    assert validate_key(key) == key


@pytest.mark.parametrize("key", ["", "   ", "a b", "x" * 40, 5, None, ["a"]])
def test_key_names_that_cannot_be_keys_are_refused(key):
    with pytest.raises(InputRejected):
        validate_key(key)


def test_hotkey_takes_a_list_not_a_string():
    """`"ctrl+a"` is a chord a human writes; the API takes the keys apart.

    Accepting the string would mean guessing the separator, and a key whose
    name contains it would silently become two keys.
    """
    with pytest.raises(InputRejected) as exc:
        validate_hotkey("ctrl+a")
    assert "list of keys" in str(exc.value)


@pytest.mark.parametrize("keys", [[], ["a"] * 20, ["a", ""], ["a", "b c"], 42, None])
def test_malformed_chords_are_refused(keys):
    with pytest.raises(InputRejected):
        validate_hotkey(keys)


def test_a_valid_chord_passes():
    assert validate_hotkey(["ctrl", "shift", "a"]) == ["ctrl", "shift", "a"]


# ---------------------------------------------------------------------------
# Drag steps
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("steps", [0, -1, 201, "10", True, 1.5, None])
def test_impossible_step_counts_are_refused(steps):
    with pytest.raises(InputRejected):
        validate_steps(steps)


def test_a_reasonable_step_count_passes():
    assert validate_steps(10) == 10


# ---------------------------------------------------------------------------
# Every backend implements the same surface
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", INPUT_METHODS)
def test_the_protocol_declares_every_input_method(name):
    assert hasattr(OSBackend, name), f"OSBackend is missing {name}"


@pytest.mark.parametrize("name", INPUT_METHODS)
def test_all_three_backends_implement_it(name):
    """`mouse_drag` is the reason this check exists.

    It was implemented in windows.py only, so the same call worked on one
    platform and raised AttributeError on the other — the shape of gap this
    parametrisation is meant to catch.
    """
    from windows_os_api.backends import fake, linux, windows

    for module, cls_name in (
        (fake, "FakeBackend"),
        (linux, "LinuxBackend"),
        (windows, "WindowsBackend"),
    ):
        cls = getattr(module, cls_name)
        assert callable(getattr(cls, name, None)), f"{cls_name} is missing {name}"


# ---------------------------------------------------------------------------
# The fake applies the same rules — it has no OS to refuse anything for it
# ---------------------------------------------------------------------------
@pytest.fixture
def fake(tmp_path):
    return FakeBackend(str(tmp_path))


def test_fake_refuses_an_unknown_button(fake):
    for call in (
        lambda: fake.mouse_click(10, 10, "rihgt"),
        lambda: fake.double_click(10, 10, "rihgt"),
        lambda: fake.mouse_drag(0, 0, 10, 10, "rihgt"),
    ):
        result = call()
        assert result["ok"] is False, result
        assert "unknown mouse button" in result["error"], result


def test_fake_refuses_an_impossible_scroll(fake):
    assert fake.scroll("diagonal", 3)["ok"] is False
    assert fake.scroll("down", 0)["ok"] is False
    assert fake.scroll("down", 101)["ok"] is False


def test_fake_refuses_a_malformed_chord(fake):
    assert fake.hotkey("ctrl+a")["ok"] is False
    assert fake.hotkey([])["ok"] is False


def test_fake_tracks_the_pointer_through_move_and_drag(fake):
    assert fake.mouse_move(120, 90)["ok"] is True
    assert fake.pointer_position() == {"x": 120, "y": 90}
    result = fake.mouse_drag(120, 90, 300, 200)
    assert result["ok"] is True
    assert fake.pointer_position() == {"x": 300, "y": 200}


def test_fake_records_a_double_click_as_two(fake):
    result = fake.double_click(10, 10)
    assert result["clicks"] == 2
    assert fake._input_log[-1]["type"] == "double_click"


def test_fake_key_down_and_key_up_are_distinct(fake):
    assert fake.key_down("shift")["state"] == "down"
    assert fake.key_up("shift")["state"] == "up"
    assert [e["type"] for e in fake._input_log[-2:]] == ["key_down", "key_up"]


# ---------------------------------------------------------------------------
# Through the HTTP surface
# ---------------------------------------------------------------------------
def test_api_double_click(client, auth_headers):
    r = client.post("/v1/input/mouse/double-click", headers=auth_headers,
                    json={"x": 10, "y": 10, "button": "left"})
    body = r.json()
    assert body["ok"] is True, body
    assert body["clicks"] == 2


def test_api_refuses_an_unknown_button(client, auth_headers):
    r = client.post("/v1/input/mouse/double-click", headers=auth_headers,
                    json={"x": 10, "y": 10, "button": "rihgt"})
    assert r.json()["ok"] is False


def test_api_scroll_and_its_refusals(client, auth_headers):
    assert client.post("/v1/input/mouse/scroll", headers=auth_headers,
                       json={"direction": "down", "amount": 3}).json()["ok"] is True
    assert client.post("/v1/input/mouse/scroll", headers=auth_headers,
                       json={"direction": "diagonal", "amount": 3}).json()["ok"] is False


def test_api_key_down_up_and_hotkey(client, auth_headers):
    assert client.post("/v1/input/keyboard/down", headers=auth_headers,
                       json={"key": "shift"}).json()["state"] == "down"
    assert client.post("/v1/input/keyboard/up", headers=auth_headers,
                       json={"key": "shift"}).json()["state"] == "up"
    assert client.post("/v1/input/keyboard/hotkey", headers=auth_headers,
                       json={"keys": ["ctrl", "a"]}).json()["chord"] == "ctrl+a"


def test_api_hotkey_refuses_a_bare_string(client, auth_headers):
    """FastAPI's own validation rejects it before the backend ever sees it."""
    r = client.post("/v1/input/keyboard/hotkey", headers=auth_headers,
                    json={"keys": "ctrl+a"})
    assert r.status_code == 422, r.text


def test_api_drag_reports_the_resulting_position(client, auth_headers):
    r = client.post("/v1/input/mouse/drag", headers=auth_headers,
                    json={"x1": 0, "y1": 0, "x2": 200, "y2": 150})
    body = r.json()
    assert body["ok"] is True, body
    assert body["position"] == {"x": 200, "y": 150}


def test_api_input_requires_ui_control(client):
    r = client.post("/v1/input/mouse/double-click", json={"x": 1, "y": 1})
    assert r.status_code in (401, 403), r.text
