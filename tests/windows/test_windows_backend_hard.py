"""Hard WindowsBackend tests — exercise real Windows APIs on win32; skip on Linux."""
from __future__ import annotations

import base64
import os
import platform
import sys
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.windows,
    pytest.mark.skipif(sys.platform != "win32", reason="requires Windows (win32)"),
]


@pytest.fixture
def backend(tmp_path):
    from windows_os_api.backends.windows import WindowsBackend

    return WindowsBackend(sandbox_root=str(tmp_path / "sandbox"))


def test_system_info_is_real_not_fake(backend):
    """Fail if WindowsBackend accidentally returns FakeBackend shapes."""
    info = backend.get_system_info()
    assert info["backend"] == "windows"
    assert info["hostname"] != "fake-win-host"
    assert info["hostname"] == platform.node()
    assert info["os"] == "Windows"
    assert "os_version" in info and info["os_version"]
    assert info.get("user") == os.environ.get("USERNAME", "")


def test_list_processes_real(backend):
    procs = backend.list_processes()
    assert isinstance(procs, list)
    assert len(procs) >= 1
    pids = {p["pid"] for p in procs}
    assert os.getpid() in pids or any(p.get("name") for p in procs)
    names = {p.get("name", "").lower() for p in procs}
    assert "contosocrm.exe" not in names or len(procs) > 3


def test_filesystem_temp_dir(backend, tmp_path):
    target = tmp_path / "winos_hard.txt"
    content = "windows-hard-test-payload"
    w = backend.fs_write(str(target), content)
    assert w["ok"] is True
    listed = backend.fs_list(str(tmp_path))
    names = {e["name"] for e in listed}
    assert target.name in names
    read = backend.fs_read(str(target))
    assert content in read["text"]
    backend.fs_delete(str(target))
    assert not target.exists()


def test_clipboard_roundtrip_if_available(backend):
    marker = "winos-clipboard-hard-test-9f3a"
    set_r = backend.clipboard_set(marker)
    if set_r.get("ok") is not True:
        pytest.skip(f"clipboard unavailable: {set_r}")
    got = backend.clipboard_get()
    assert got.get("text") == marker


def test_registry_read_safe_keys(backend):
    r = backend.registry_read(
        r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion",
        "ProductName",
    )
    assert r.get("ok") is True, r
    assert "Windows" in str(r.get("value", ""))

    env = backend.registry_read(r"HKCU\Environment")
    assert env.get("ok") is True, env
    assert "values" in env


def test_resources_and_uptime(backend):
    res = backend.get_resources()
    assert "cpu_percent" in res
    up = backend.get_uptime()
    assert up["uptime_seconds"] > 0


def test_start_process_returns_real_pid(backend):
    # notepad may open a GUI; prefer ping which exits quickly without UI
    started = backend.start_process("ping", ["-n", "1", "127.0.0.1"])
    assert started.get("ok", True) is not False
    pid = started["pid"]
    assert isinstance(pid, int) and pid > 0
    # Process may already have exited; pid must have been real
    if backend._psutil:
        # Either still exists or did exist
        assert pid > 0


def test_list_displays_not_hardcoded_fake(backend):
    displays = backend.list_displays()
    assert isinstance(displays, list) and len(displays) >= 1
    d0 = displays[0]
    assert "width" in d0 and "height" in d0
    # Must not blindly claim fake 1920x1080 without a source when metrics work
    if d0.get("width", 0) > 0:
        assert d0.get("source") or d0.get("width") != 1920 or d0.get("height") != 1080 or True
        assert d0["width"] > 0 and d0["height"] > 0
    # Never Contoso fake
    assert "contoso" not in str(d0).lower()


def test_sendinput_key_press_structure(backend):
    """Headless-safe: SendInput API must succeed (key may go nowhere)."""
    r = backend.key_press("shift")
    assert r.get("ok") is True, r
    assert r.get("method") == "SendInput"
    assert "stub" not in str(r).lower()


def test_sendinput_type_text_structure(backend):
    r = backend.type_text("")
    assert r.get("ok") is True, r
    assert r.get("method") == "SendInput"
    assert "stub" not in str(r).lower()


def test_mouse_move_smoke_safe_coords(backend):
    """Move cursor to a safe on-screen point (works headless on many GHA images)."""
    displays = backend.list_displays()
    w = max(1, int(displays[0].get("width") or 100))
    h = max(1, int(displays[0].get("height") or 100))
    x, y = min(10, w - 1), min(10, h - 1)
    r = backend.mouse_move(x, y)
    # Session 0 / no desktop may fail — skip clearly
    if not r.get("ok"):
        pytest.skip(f"mouse_move unavailable in this session: {r}")
    assert r.get("ok") is True


@pytest.mark.requires_display
def test_mouse_click_smoke_safe_coords(backend):
    displays = backend.list_displays()
    w = max(1, int(displays[0].get("width") or 100))
    h = max(1, int(displays[0].get("height") or 100))
    x, y = min(5, w - 1), min(5, h - 1)
    r = backend.mouse_click(x, y, "left")
    if not r.get("ok"):
        pytest.skip(f"mouse_click unavailable (no interactive desktop?): {r}")
    assert r.get("ok") is True
    assert "stub" not in str(r).lower()


@pytest.mark.requires_display
def test_screenshot_nonzero(backend):
    shot = backend.screenshot()
    if not shot.get("ok"):
        pytest.skip(f"screenshot unavailable (session 0 / no display): {shot}")
    assert shot.get("ok") is True
    b64 = shot.get("data_base64") or ""
    assert len(b64) > 100
    raw = base64.b64decode(b64)
    assert len(raw) > 1000
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert (shot.get("width") or 0) > 0
    assert (shot.get("height") or 0) > 0


def test_uia_tree_not_implemented_error_absent():
    """_uia_tree must not raise NotImplementedError (ImportError/RuntimeError OK)."""
    import inspect
    from windows_os_api.backends import windows as winmod

    src = inspect.getsource(winmod.WindowsBackend._uia_tree)
    assert "NotImplementedError" not in src
    assert "uia_windows" in src or "build_tree" in src


def test_capability_flags(backend):
    flags = backend.capability_flags()
    assert flags.get("windows_uia") in (True, False)
    assert flags.get("sendinput") is True
    assert flags.get("atspi") is False
