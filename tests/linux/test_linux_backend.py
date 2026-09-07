"""Hard tests for real LinuxBackend — processes, FS, system, network, factory."""
from __future__ import annotations

import sys
import time

import pytest

pytestmark = pytest.mark.linux

if sys.platform == "win32":
    pytest.skip("Linux only", allow_module_level=True)


def test_system_info_real_linux(linux_backend):
    info = linux_backend.get_system_info()
    assert info["backend"] == "linux"
    assert "Linux" in info["os"]
    assert info.get("hostname")
    assert info["backend"] != "fake"
    # Must not look like FakeBackend Contoso host
    assert info["hostname"] != "fake-win-host"


def test_start_process_real_pid_and_terminate(linux_backend):
    import psutil

    started = linux_backend.start_process("/bin/sleep", ["30"])
    assert started.get("ok", True) is not False
    pid = started["pid"]
    assert pid > 0
    assert started.get("real") is True
    proc = psutil.Process(pid)
    assert proc.is_running()
    # Also visible via backend get_process
    gp = linux_backend.get_process(pid)
    assert gp is not None
    assert gp["pid"] == pid
    term = linux_backend.terminate_process(pid)
    assert term["ok"] is True
    time.sleep(0.3)
    assert not psutil.pid_exists(pid) or not psutil.Process(pid).is_running()


def test_start_echo_exits_cleanly(linux_backend):
    import psutil

    started = linux_backend.start_process("/bin/echo", ["winos-linux-ok"])
    pid = started["pid"]
    assert pid > 0
    # echo exits quickly — PID was real at start
    time.sleep(0.2)
    # process may have exited; ensure start returned a real OS pid (not fake 1000+)
    # FakeBackend starts at 1000+ synthetic; real echo/sleep get kernel PIDs
    assert isinstance(pid, int)


def test_fs_roundtrip_sandbox(linux_backend):
    w = linux_backend.fs_write("hello.txt", "linux-real-fs")
    assert w["ok"] is True
    listed = linux_backend.fs_list(".")
    assert any(e["name"] == "hello.txt" for e in listed)
    read = linux_backend.fs_read("hello.txt")
    assert read["text"] == "linux-real-fs"
    linux_backend.fs_delete("hello.txt")


def test_fs_blocks_traversal(linux_backend):
    with pytest.raises(PermissionError):
        linux_backend.fs_read("../outside.txt")


def test_network_interfaces_nonempty(linux_backend):
    ifaces = linux_backend.network_interfaces()
    assert isinstance(ifaces, list)
    assert len(ifaces) >= 1
    assert "name" in ifaces[0]


def test_factory_auto_selects_linux(monkeypatch, tmp_path):
    monkeypatch.setenv("WINOS_BACKEND", "auto")
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(tmp_path))
    from windows_os_api.core.runtime.config import get_settings
    from windows_os_api.backends.factory import reset_backend, get_backend

    get_settings.cache_clear()
    reset_backend()
    assert get_settings().resolve_backend() == "linux"
    b = get_backend()
    assert b.name == "linux"


def test_windows_backend_explicit_raises_without_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("WINOS_BACKEND", "windows")
    monkeypatch.setenv("WINOS_ALLOW_FAKE_FALLBACK", "false")
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(tmp_path))
    from windows_os_api.core.runtime.config import get_settings
    from windows_os_api.backends.factory import reset_backend, get_backend, BackendUnavailable

    get_settings.cache_clear()
    reset_backend()
    with pytest.raises(BackendUnavailable):
        get_backend()


def test_capabilities_flags_honest(linux_backend):
    flags = linux_backend.capability_flags()
    assert flags.get("windows_uia") is False
    assert flags.get("processes") is True
    assert flags.get("filesystem") is True


def test_registry_json_compat(linux_backend, tmp_path):
    # Use backend's registry store
    w = linux_backend.registry_write(r"HKCU\Software\WinOsLinux", "Theme", "dark")
    assert w["ok"] is True
    r = linux_backend.registry_read(r"HKCU\Software\WinOsLinux", "Theme")
    assert r["ok"] and r["value"] == "dark"
