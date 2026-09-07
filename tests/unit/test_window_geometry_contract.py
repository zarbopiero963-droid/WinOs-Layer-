"""The window geometry contract, in the places every backend has to honour it.

The real behaviour is covered where it is real — tests/linux against an X
window under Xvfb, tests/windows against a live HWND. This suite covers the two
things that must hold identically everywhere, and that neither of those can
check on its own:

* the validator refuses the same inputs on every platform, before anything acts;
* the three backends implement the same surface, so a caller cannot get a
  different contract by reaching a different one.
"""
from __future__ import annotations

import pytest

from windows_os_api.backends.base import OSBackend
from windows_os_api.backends.fake import FakeBackend
from windows_os_api.os.windows.geometry import (
    GeometryRejected,
    validate_position,
    validate_size,
)

GEOMETRY_METHODS = (
    "window_geometry",
    "move_window",
    "resize_window",
    "minimize_window",
    "maximize_window",
    "restore_window",
)


# ---------------------------------------------------------------------------
# The validator
# ---------------------------------------------------------------------------
def test_a_reasonable_request_passes_through_unchanged():
    assert validate_position(300, 200) == (300, 200)
    assert validate_size(700, 500) == (700, 500)


def test_negative_coordinates_are_allowed():
    """A window partly off-screen is a legitimate thing to ask for."""
    assert validate_position(-100, -50) == (-100, -50)


@pytest.mark.parametrize("width,height", [(0, 100), (100, 0), (-1, 100), (100, -1)])
def test_zero_and_negative_sizes_are_refused_not_clamped(width, height):
    """Refused, not silently corrected.

    Clamping would hand the caller a window of a size they never asked for and
    report success — the request would be reinterpreted rather than answered.
    """
    with pytest.raises(GeometryRejected) as exc:
        validate_size(width, height)
    assert "out of range" in str(exc.value)


@pytest.mark.parametrize(
    "x,y", [(32768, 0), (0, 32768), (-32769, 0), (0, -32769), (10**9, 0)]
)
def test_coordinates_outside_the_x11_range_are_refused(x, y):
    with pytest.raises(GeometryRejected):
        validate_position(x, y)


def test_a_size_beyond_the_x11_range_is_refused():
    with pytest.raises(GeometryRejected):
        validate_size(32768, 100)


@pytest.mark.parametrize("value", [True, False])
def test_a_bool_is_not_accepted_as_a_coordinate(value):
    """`bool` subclasses `int`, so True would otherwise arrive as 1.

    Reinterpreting a caller's `true` as `1` is not leniency, it is answering a
    question they did not ask.
    """
    with pytest.raises(GeometryRejected) as exc:
        validate_position(value, 0)
    assert "must be an integer" in str(exc.value)


@pytest.mark.parametrize("value", ["300", 3.5, None, [300]])
def test_non_integers_are_refused(value):
    with pytest.raises(GeometryRejected):
        validate_position(value, 0)
    with pytest.raises(GeometryRejected):
        validate_size(value, 100)


# ---------------------------------------------------------------------------
# Every backend implements the same surface
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", GEOMETRY_METHODS)
def test_the_protocol_declares_every_geometry_method(name):
    assert hasattr(OSBackend, name), f"OSBackend is missing {name}"


@pytest.mark.parametrize("name", GEOMETRY_METHODS)
def test_all_three_backends_implement_it(name):
    """A method missing from one backend is a caller getting a different API.

    Imported lazily and by module, so this runs on Linux: `windows.py` imports
    fine off win32, it only refuses to *act* there.
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
# The fake honours the contract — including the parts that are inconvenient
# ---------------------------------------------------------------------------
@pytest.fixture
def fake(tmp_path):
    return FakeBackend(str(tmp_path))


def test_fake_reports_the_result_separately_from_the_request(fake):
    """The fake must NOT echo the request back as the result.

    A window manager adds a frame offset — measured at (2, 40) under openbox —
    so on both real platforms `geometry != requested`. If the fake returned
    them equal, a test written against it could assert equality, pass here, and
    be wrong everywhere that matters.
    """
    result = fake.move_window(1001, 300, 200)
    assert result["ok"] is True
    assert result["requested"] == {"x": 300, "y": 200}
    assert (result["geometry"]["x"], result["geometry"]["y"]) != (300, 200), result


def test_fake_geometry_agrees_with_a_fresh_read(fake):
    result = fake.move_window(1001, 300, 200)
    assert result["geometry"] == fake.window_geometry(1001)


def test_fake_applies_the_same_validation_as_the_real_backends(fake):
    result = fake.resize_window(1001, 0, 100)
    assert result["ok"] is False
    assert "out of range" in result["error"]


def test_fake_refuses_an_unknown_window(fake):
    for call in (
        lambda: fake.move_window(999999, 1, 1),
        lambda: fake.resize_window(999999, 100, 100),
        lambda: fake.minimize_window(999999),
        lambda: fake.maximize_window(999999),
        lambda: fake.restore_window(999999),
    ):
        result = call()
        assert result["ok"] is False, result
        assert "not found" in result["error"], result


def test_fake_state_round_trips(fake):
    assert fake.minimize_window(1001)["state"] == "minimized"
    assert fake.get_window(1001)["visible"] is False
    assert fake.maximize_window(1001)["state"] == "maximized"
    assert fake.restore_window(1001)["state"] == "normal"
    assert fake.get_window(1001)["visible"] is True


def test_a_refused_resize_leaves_the_window_untouched(fake):
    before = fake.window_geometry(1001)
    fake.resize_window(1001, -1, -1)
    assert fake.window_geometry(1001) == before


# ---------------------------------------------------------------------------
# An unreadable state is never a verified one
#
# This is the shape of a real failure, caught by the first CI run of this
# feature: `maximize` and `restore` came back `{"ok": false, "state": null}` —
# and, before the fix, `"verified": true` beside it, with nothing saying why.
# The cause was Windows-only (pywin32 does not reliably expose `IsZoomed`), but
# the reporting rule it broke is platform-independent, so it is pinned here
# where it runs everywhere rather than only on the runner that found it.
# ---------------------------------------------------------------------------
SW_SHOWNORMAL, SW_SHOWMINIMIZED, SW_SHOWMAXIMIZED = 1, 2, 3
SW_MINIMIZE, SW_SHOWMINNOACTIVE, SW_RESTORE = 6, 7, 9


class _StubWin32Gui:
    """Just enough win32gui to drive `_show_window`'s reporting branches."""

    def __init__(self, *, placement_fails: bool = False, iconic: bool = False,
                 show_cmd: int = SW_SHOWNORMAL, is_window: bool = True):
        self.placement_fails = placement_fails
        self.iconic = iconic
        self.show_cmd = show_cmd
        self.is_window = is_window
        self.shown: list[int] = []

    def IsWindow(self, hwnd):  # noqa: N802
        return self.is_window

    def GetWindowRect(self, hwnd):  # noqa: N802
        return (0, 0, 800, 600)

    def IsIconic(self, hwnd):  # noqa: N802
        return self.iconic

    def ShowWindow(self, hwnd, cmd):  # noqa: N802
        self.shown.append(cmd)

    def GetWindowPlacement(self, hwnd):  # noqa: N802
        if self.placement_fails:
            # The shape of what the runner actually did: the attribute the old
            # code reached for was not there, and the exception became a silent
            # `None` with `verified: true` sitting next to it.
            raise AttributeError("module 'win32gui' has no attribute 'IsZoomed'")
        return (0, self.show_cmd, (0, 0), (0, 0), (0, 0, 800, 600))


@pytest.fixture
def win32con_stub(monkeypatch):
    """`win32con` does not exist off win32; the constants it carries do."""
    import sys
    import types

    module = types.ModuleType("win32con")
    for name, value in (
        ("SW_SHOWNORMAL", SW_SHOWNORMAL), ("SW_SHOWMINIMIZED", SW_SHOWMINIMIZED),
        ("SW_SHOWMAXIMIZED", SW_SHOWMAXIMIZED), ("SW_MAXIMIZE", SW_SHOWMAXIMIZED),
        ("SW_MINIMIZE", SW_MINIMIZE), ("SW_SHOWMINNOACTIVE", SW_SHOWMINNOACTIVE),
        ("SW_RESTORE", SW_RESTORE),
    ):
        setattr(module, name, value)
    monkeypatch.setitem(sys.modules, "win32con", module)
    return module


def _windows_backend_with(stub):
    """A WindowsBackend without its win32 constructor — Linux can still run this."""
    from windows_os_api.backends.windows import WindowsBackend

    backend = object.__new__(WindowsBackend)
    backend._win32gui = stub
    return backend


def test_an_unreadable_state_is_never_reported_as_verified(win32con_stub):
    """The exact regression the first Windows CI run produced.

    `{"ok": false, "state": null, "verified": true}` claims a confirmation that
    never happened, and says nothing about why it could not be made.
    """
    stub = _StubWin32Gui(placement_fails=True)
    result = _windows_backend_with(stub).maximize_window(1234)

    assert result["ok"] is False
    assert result["state"] is None
    assert result["verified"] is False, result
    assert "could not determine window state" in result["error"], result
    assert "IsZoomed" in result["error"], result  # the reason travels with it
    assert stub.shown == [SW_SHOWMAXIMIZED], "the operation itself still ran"


def test_a_readable_state_is_reported_as_verified(win32con_stub):
    result = _windows_backend_with(_StubWin32Gui(show_cmd=SW_SHOWMAXIMIZED)).maximize_window(1)
    assert result["ok"] is True
    assert result["state"] == "maximized"
    assert result["verified"] is True


def test_the_state_reader_no_longer_depends_on_iszoomed():
    """`IsZoomed` is what the three failing tests had in common.

    pywin32 does not reliably expose it — the stub above has no such attribute
    either, and neither does the runner's build.
    """
    import inspect

    from windows_os_api.backends import windows as win_backend

    body = inspect.getsource(win_backend.WindowsBackend._window_state)
    code = body.split('"""')[-1]  # the docstring names it; the code must not use it
    assert "IsZoomed" not in code, code


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"show_cmd": SW_SHOWMAXIMIZED}, "maximized"),
        ({"iconic": True}, "minimized"),
        ({"show_cmd": SW_SHOWMINIMIZED}, "minimized"),
        ({"show_cmd": SW_MINIMIZE}, "minimized"),
        ({"show_cmd": SW_SHOWMINNOACTIVE}, "minimized"),
        ({"show_cmd": SW_SHOWNORMAL}, "normal"),
    ],
)
def test_the_state_reader_classifies_every_placement(win32con_stub, kwargs, expected):
    assert _windows_backend_with(_StubWin32Gui(**kwargs))._window_state(1) == (expected, None)


def test_the_state_reader_reports_a_missing_window(win32con_stub):
    state, why = _windows_backend_with(_StubWin32Gui(is_window=False))._window_state(1)
    assert state is None
    assert "not found" in why


# ---------------------------------------------------------------------------
# Through the HTTP surface
# ---------------------------------------------------------------------------
def test_api_move_returns_requested_and_geometry(client, auth_headers):
    r = client.post("/v1/windows/1001/move", headers=auth_headers, json={"x": 300, "y": 200})
    body = r.json()
    assert body["ok"] is True, body
    assert body["requested"] == {"x": 300, "y": 200}
    assert set(body["geometry"]) == {"x", "y", "width", "height"}


def test_api_resize_refuses_a_zero_dimension(client, auth_headers):
    r = client.post(
        "/v1/windows/1001/resize", headers=auth_headers, json={"width": 0, "height": 100}
    )
    assert r.json()["ok"] is False


def test_api_rejects_a_non_integer_dimension(client, auth_headers):
    """FastAPI's own validation is the first gate; it must not coerce silently."""
    r = client.post(
        "/v1/windows/1001/resize",
        headers=auth_headers,
        json={"width": "not-a-number", "height": 100},
    )
    assert r.status_code == 422, r.text


def test_api_state_endpoints_round_trip(client, auth_headers):
    assert client.post("/v1/windows/1001/minimize", headers=auth_headers).json()[
        "state"
    ] == "minimized"
    assert client.post("/v1/windows/1001/maximize", headers=auth_headers).json()[
        "state"
    ] == "maximized"
    assert client.post("/v1/windows/1001/restore", headers=auth_headers).json()[
        "state"
    ] == "normal"


def test_api_geometry_endpoints_require_ui_control(client):
    """No key, no move. The read endpoints and the control endpoints differ."""
    r = client.post("/v1/windows/1001/move", json={"x": 1, "y": 1})
    assert r.status_code in (401, 403), r.text
