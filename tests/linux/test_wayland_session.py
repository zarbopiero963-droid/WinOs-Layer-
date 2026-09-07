"""Unit tests for Wayland/X11 session detection + command construction."""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.linux

if sys.platform == "win32":
    pytest.skip("Linux session tests", allow_module_level=True)


def test_detect_x11_session():
    from windows_os_api.backends.linux_session import detect_session

    s = detect_session({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0", "WAYLAND_DISPLAY": ""})
    assert s["x11"] is True
    assert s["session_type"] == "x11"


def test_detect_wayland_session():
    from windows_os_api.backends.linux_session import detect_session

    s = detect_session(
        {
            "XDG_SESSION_TYPE": "wayland",
            "WAYLAND_DISPLAY": "wayland-0",
            "DISPLAY": "",
            "XDG_CURRENT_DESKTOP": "sway",
        }
    )
    assert s["wayland"] is True
    assert s["compositor"] == "sway"


def test_detect_hyprland():
    from windows_os_api.backends.linux_session import detect_session

    s = detect_session(
        {
            "XDG_SESSION_TYPE": "wayland",
            "WAYLAND_DISPLAY": "wayland-1",
            "HYPRLAND_INSTANCE_SIGNATURE": "abc",
        }
    )
    assert s["compositor"] == "hyprland"


def test_wayland_type_cmd_construction(monkeypatch):
    from windows_os_api.backends import linux_session as ls

    monkeypatch.setattr(ls.shutil, "which", lambda n: "/usr/bin/" + n if n == "ydotool" else None)
    cmd = ls.build_wayland_type_cmd("hello", tool="ydotool")
    assert cmd == ["ydotool", "type", "--", "hello"]
    cmd2 = ls.build_wayland_type_cmd("hi", tool="wtype")
    assert cmd2 == ["wtype", "--", "hi"]


def test_wayland_click_cmd_ydotool():
    from windows_os_api.backends.linux_session import build_wayland_click_cmd

    cmd = build_wayland_click_cmd(10, 20, button="left", tool="ydotool")
    assert cmd is not None
    assert cmd[0] == "ydotool"
    assert "10" in cmd and "20" in cmd


def test_wayland_click_wtype_unsupported():
    from windows_os_api.backends.linux_session import build_wayland_click_cmd

    assert build_wayland_click_cmd(1, 2, tool="wtype") is None


def test_wayland_list_windows_cmds():
    from windows_os_api.backends.linux_session import build_wayland_list_windows_cmd

    assert build_wayland_list_windows_cmd("swaymsg") == ["swaymsg", "-t", "get_tree"]
    assert build_wayland_list_windows_cmd("hyprctl")[0] == "hyprctl"
    assert build_wayland_list_windows_cmd("wlrctl")[0] == "wlrctl"


def test_probe_flags_structure():
    from windows_os_api.backends.linux_session import probe_wayland_capabilities

    p = probe_wayland_capabilities({"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"})
    assert p["wayland"] is True
    assert "notes" in p
    assert "portal" in p["notes"]


@pytest.mark.skipif(
    (__import__("os").environ.get("XDG_SESSION_TYPE") or "").lower() != "wayland"
    and not __import__("os").environ.get("WAYLAND_DISPLAY"),
    reason="not on live Wayland session",
)
def test_live_wayland_optional(linux_backend):
    caps = linux_backend.capability_flags()
    assert caps.get("wayland") is True
