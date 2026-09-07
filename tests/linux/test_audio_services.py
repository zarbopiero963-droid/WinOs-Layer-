"""Hard tests for audio + systemd helpers with mocked subprocess."""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.linux

if sys.platform == "win32":
    pytest.skip("Linux audio/services", allow_module_level=True)


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_parse_list_units():
    from windows_os_api.backends.linux_services import parse_list_units

    text = (
        "foo.service loaded active running Foo Service\n"
        "bar.service loaded inactive dead Bar\n"
        "not-a-unit something\n"
    )
    rows = parse_list_units(text, scope="user")
    assert len(rows) == 2
    assert rows[0]["name"] == "foo"
    assert rows[0]["scope"] == "user"
    assert rows[1]["status"] == "inactive"


def test_control_service_rejects_metacharacters():
    from windows_os_api.backends.linux_services import control_service

    r = control_service("evil;rm", "start", scope="user", run=lambda *a, **k: _Proc())
    assert r["ok"] is False
    assert r.get("denied") is True


def test_control_service_mocked(monkeypatch):
    from windows_os_api.backends import linux_services as ls

    monkeypatch.setattr(ls.shutil, "which", lambda n: "/bin/systemctl" if n == "systemctl" else None)
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return _Proc(stdout="active", returncode=0)

    r = ls.control_service("dbus", "status", scope="user", run=run)
    assert r["ok"] is True
    assert calls[0][:3] == ["systemctl", "--user", "status"]


def test_control_service_system_scope_mocked(monkeypatch):
    from windows_os_api.backends import linux_services as ls

    monkeypatch.setattr(ls.shutil, "which", lambda n: "/bin/systemctl" if n == "systemctl" else None)

    def run(argv, **kw):
        assert "--user" not in argv
        return _Proc(returncode=0)

    r = ls.control_service("ssh", "status", scope="system", run=run)
    assert r["ok"] is True


def test_list_services_mocked(monkeypatch):
    from windows_os_api.backends import linux_services as ls

    monkeypatch.setattr(ls.shutil, "which", lambda n: "/bin/systemctl" if n == "systemctl" else None)

    def run(argv, **kw):
        if "--user" in argv:
            return _Proc("myapp.service loaded active running My App\n")
        return _Proc("ssh.service loaded active running OpenSSH\n")

    rows = ls.list_services(run=run)
    names = {r["name"] for r in rows}
    assert "myapp" in names
    assert "ssh" in names


def test_audio_devices_pactl_mocked(monkeypatch):
    from windows_os_api.backends import linux_audio as la

    monkeypatch.setattr(la.shutil, "which", lambda n: "/usr/bin/pactl" if n == "pactl" else None)

    def run(argv, **kw):
        if "sinks" in argv:
            return _Proc("0\talsa_output.pci\tmodule-alsa\t16\tSUSPENDED\n")
        if "sources" in argv:
            return _Proc("1\talsa_input.pci\tmodule-alsa\t16\tSUSPENDED\n")
        return _Proc()

    devs = la.list_devices(run=run)
    assert any(d["type"] == "output" for d in devs)
    assert any(d["type"] == "input" for d in devs)


def test_audio_volume_wpctl_mocked(monkeypatch):
    from windows_os_api.backends import linux_audio as la

    monkeypatch.setattr(
        la.shutil, "which", lambda n: "/usr/bin/wpctl" if n == "wpctl" else None
    )

    def run(argv, **kw):
        return _Proc("Volume: 0.42 [MUTED]\n")

    vol = la.get_volume(run=run)
    assert vol["volume"] == 42
    assert vol["muted"] is True
    assert vol["backend"] == "wpctl"


def test_set_volume_mute_pactl_mocked(monkeypatch):
    from windows_os_api.backends import linux_audio as la

    monkeypatch.setattr(la.shutil, "which", lambda n: "/usr/bin/pactl" if n == "pactl" else None)
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        return _Proc(returncode=0)

    assert la.set_volume(33, run=run)["ok"] is True
    assert any("33%" in a for a in seen[0])
    assert la.set_mute(True, run=run)["ok"] is True


@pytest.mark.skipif(
    __import__("shutil").which("pactl") is None and __import__("shutil").which("wpctl") is None,
    reason="no audio tools",
)
def test_live_audio_optional(linux_backend):
    vol = linux_backend.audio_volume()
    assert "volume" in vol


@pytest.mark.skipif(
    __import__("shutil").which("systemctl") is None,
    reason="systemctl missing",
)
def test_live_services_optional(linux_backend):
    svcs = linux_backend.list_services()
    assert isinstance(svcs, list)
    assert svcs
