"""The focus/close contract, in the places every backend has to honour it.

Real behaviour is covered where it is real — tests/linux against X windows
under Xvfb, tests/windows against live HWNDs. This suite covers what must hold
identically everywhere: the error codes, the fake answering exactly as the real
backends do, and the HTTP surface carrying the code through.
"""
from __future__ import annotations

import pytest

from windows_os_api.backends.base import OSBackend
from windows_os_api.backends.fake import FakeBackend
from windows_os_api.os.windows.errors import (
    FOCUS_NOT_GRANTED,
    TOOL_UNAVAILABLE,
    WINDOW_NOT_FOUND,
    WINDOW_STILL_OPEN,
    failure,
)

GHOST = 99999999


# ---------------------------------------------------------------------------
# The codes themselves
# ---------------------------------------------------------------------------
def test_a_failure_carries_both_the_code_and_the_message():
    """`error_code` is added ALONGSIDE `error`, never instead of it.

    The rest of the repository reports failures as free text, and callers that
    read `error` must keep working. A caller that has to tell "not found" from
    "still open" by matching English prose is coupled to the wording; one that
    branches on the code is not.
    """
    result = failure(WINDOW_NOT_FOUND, "window 7 not found", hwnd=7)
    assert result["ok"] is False
    assert result["error_code"] == WINDOW_NOT_FOUND
    assert result["error"] == "window 7 not found"
    assert result["hwnd"] == 7


@pytest.mark.parametrize(
    "code", [WINDOW_NOT_FOUND, WINDOW_STILL_OPEN, FOCUS_NOT_GRANTED, TOOL_UNAVAILABLE]
)
def test_codes_are_stable_strings(code):
    """Pinned because they are part of the API contract now.

    Renaming one silently would break every caller branching on it, and nothing
    else in the suite would notice.
    """
    assert code.isupper()
    assert code.replace("_", "").isalpha()


def test_the_four_codes_are_distinct():
    codes = {WINDOW_NOT_FOUND, WINDOW_STILL_OPEN, FOCUS_NOT_GRANTED, TOOL_UNAVAILABLE}
    assert len(codes) == 4


# ---------------------------------------------------------------------------
# Every backend implements the same surface
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["focus_window", "close_window", "active_window"])
def test_the_protocol_declares_it(name):
    assert hasattr(OSBackend, name), f"OSBackend is missing {name}"


@pytest.mark.parametrize("name", ["focus_window", "close_window", "active_window"])
def test_all_three_backends_implement_it(name):
    from windows_os_api.backends import fake, linux, windows

    for module, cls_name in (
        (fake, "FakeBackend"), (linux, "LinuxBackend"), (windows, "WindowsBackend")
    ):
        assert callable(getattr(getattr(module, cls_name), name, None))


def test_close_window_takes_a_timeout_on_every_backend():
    """Closing WAITS, so the wait has to be bounded — and bounded by the caller.

    A method that polls forever is worse than one that lies quickly.
    """
    import inspect

    from windows_os_api.backends import fake, linux, windows

    for module, cls_name in (
        (fake, "FakeBackend"), (linux, "LinuxBackend"), (windows, "WindowsBackend")
    ):
        signature = inspect.signature(getattr(getattr(module, cls_name), "close_window"))
        assert "timeout" in signature.parameters, cls_name


# ---------------------------------------------------------------------------
# The fake answers exactly as the real backends do
# ---------------------------------------------------------------------------
@pytest.fixture
def fake(tmp_path):
    return FakeBackend(str(tmp_path))


def test_fake_refuses_an_unknown_window_with_the_same_code(fake):
    """It always refused — but with a bare message and no code.

    A test written against the fake would have learned a contract the real
    backends do not honour, which is the failure mode this repository has hit
    before.
    """
    for result in (fake.focus_window(GHOST), fake.close_window(GHOST)):
        assert result["ok"] is False, result
        assert result["error_code"] == WINDOW_NOT_FOUND, result
        assert str(GHOST) in result["error"]


def test_fake_focus_reports_the_active_window(fake):
    result = fake.focus_window(1002)
    assert result["ok"] is True, result
    assert result["active_window"] == 1002
    assert result["verified"] is True
    assert fake.active_window() == 1002


def test_fake_focus_moves_between_windows(fake):
    fake.focus_window(1001)
    assert fake.active_window() == 1001
    fake.focus_window(1002)
    assert fake.active_window() == 1002


def test_fake_close_removes_the_window(fake):
    result = fake.close_window(1002)
    assert result["ok"] is True, result
    assert result["closed"] is True
    assert fake.get_window(1002) is None


def test_fake_models_a_window_that_refuses_to_close(fake):
    """The WINDOW_STILL_OPEN branch, reachable deterministically.

    Nothing available on the Linux side ignores WM_DELETE_WINDOW — xterm closes
    and so does xev — so without the fake modelling one, this outcome could not
    be exercised against a backend at all. An unsaved document is the everyday
    version of it.
    """
    fake._windows[1001]["refuses_close"] = True
    result = fake.close_window(1001, timeout=0.1)
    assert result["ok"] is False, result
    assert result["error_code"] == WINDOW_STILL_OPEN, result
    assert result["closed"] is False
    # Refused is not gone: the window is still there afterwards.
    assert fake.get_window(1001) is not None


# ---------------------------------------------------------------------------
# Through the HTTP surface
# ---------------------------------------------------------------------------
def test_api_focus_and_close_report_the_code(client, auth_headers):
    ghost = client.post(f"/v1/windows/{GHOST}/focus", headers=auth_headers).json()
    assert ghost["ok"] is False, ghost
    assert ghost["error_code"] == WINDOW_NOT_FOUND, ghost

    deleted = client.delete(f"/v1/windows/{GHOST}", headers=auth_headers).json()
    assert deleted["ok"] is False, deleted
    assert deleted["error_code"] == WINDOW_NOT_FOUND, deleted


def test_api_focus_succeeds_on_a_real_window(client, auth_headers):
    body = client.post("/v1/windows/1002/focus", headers=auth_headers).json()
    assert body["ok"] is True, body
    assert body["active_window"] == 1002


def test_api_close_succeeds_and_the_window_is_gone(client, auth_headers):
    body = client.delete("/v1/windows/1002", headers=auth_headers).json()
    assert body["ok"] is True, body
    assert body["closed"] is True
    assert client.get("/v1/windows/1002", headers=auth_headers).status_code == 404
