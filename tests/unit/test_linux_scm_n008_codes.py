"""N008 Linux parity: distinct codes for not_found / permission / timeout."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from windows_os_api.backends import linux_services as ls


@pytest.fixture(autouse=True)
def _fake_systemctl(monkeypatch):
    """This box may lack systemctl; tests inject run() and only need the binary present."""
    monkeypatch.setattr(ls.shutil, "which", lambda name: "/bin/systemctl" if name == "systemctl" else None)


def _proc(code=1, stdout="", stderr=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def test_permission_denied_code():
    out = ls.control_service(
        "ssh", "restart", scope="system",
        run=lambda *a, **k: _proc(stderr="Access denied"),
    )
    assert out["ok"] is False
    assert out["code"] == "permission_denied"
    assert out.get("denied") is True


def test_not_found_code():
    out = ls.control_service(
        "nope", "restart", scope="user",
        run=lambda *a, **k: _proc(stderr="Unit nope.service could not be found."),
    )
    assert out["code"] == "not_found"


def test_timeout_from_stderr():
    out = ls.control_service(
        "slow", "stop", scope="user",
        run=lambda *a, **k: _proc(stderr="Job timed out."),
    )
    assert out["code"] == "timeout"


def test_subprocess_timeout_expired():
    def run(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=15)

    out = ls.control_service("slow", "restart", scope="user", run=run)
    assert out["ok"] is False
    assert out["code"] == "timeout"


def test_restart_success():
    out = ls.control_service(
        "dbus", "restart", scope="user",
        run=lambda *a, **k: _proc(code=0, stdout="ok"),
    )
    assert out["ok"] is True
    assert out["action"] == "restart"
