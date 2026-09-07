"""Hard WindowsBackend tests — exercise real Windows APIs on win32; skip on Linux."""
from __future__ import annotations

import os
import platform
import sys
import tempfile
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
    # FakeBackend uses fixed Contoso-ish values; real must use USERNAME
    assert info.get("user") == os.environ.get("USERNAME", "")


def test_list_processes_real(backend):
    procs = backend.list_processes()
    assert isinstance(procs, list)
    assert len(procs) >= 1
    pids = {p["pid"] for p in procs}
    assert os.getpid() in pids or any(p.get("name") for p in procs)
    # Must not be the FakeBackend canned set alone
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
    # CurrentVersion is always present on Windows
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
