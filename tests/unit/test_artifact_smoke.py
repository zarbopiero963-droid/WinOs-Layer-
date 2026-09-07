"""Hard tests for artifact_smoke logic — the guard that proves the frozen binary runs.

These lock in the fail-closed behaviour. The whole point of artifact_smoke is to
turn "the build produced nothing usable" into a red CI job; if any of these
regress, the smoke would go green on a broken or absent artifact and we would be
back to shipping an EXE nobody ever launched.
"""
from __future__ import annotations

import importlib.util
import socket
from pathlib import Path

import pytest


@pytest.fixture
def smoke():
    """Load artifact_smoke by path so scripts/ need not be a package."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "artifact_smoke.py"
    spec = importlib.util.spec_from_file_location("artifact_smoke", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_binary_name_per_platform(smoke):
    assert smoke.binary_name("win32") == "winos-api.exe"
    assert smoke.binary_name("linux") == "winos-api"
    assert smoke.binary_name("darwin") == "winos-api"


def test_resolve_binary_finds_artifact(smoke, tmp_path):
    (tmp_path / "winos-api").write_bytes(b"ELF")
    assert smoke.resolve_binary(tmp_path, "linux").name == "winos-api"

    (tmp_path / "winos-api.exe").write_bytes(b"MZ")
    assert smoke.resolve_binary(tmp_path, "win32").name == "winos-api.exe"


def test_missing_artifact_raises_never_passes(smoke, tmp_path):
    """A missing binary must fail loudly — never skip, never return None.

    This is the regression that would silently defeat the whole smoke: if a
    missing artifact were tolerated, a build that produced nothing would still
    report success.
    """
    with pytest.raises(smoke.SmokeError) as exc:
        smoke.resolve_binary(tmp_path, "linux")
    assert "artifact not found" in str(exc.value)


def test_missing_artifact_error_lists_what_was_there(smoke, tmp_path):
    """The failure must name what dist/ actually held, or debugging is guesswork."""
    (tmp_path / "winos-api-portable-windows.zip").write_bytes(b"PK")
    with pytest.raises(smoke.SmokeError) as exc:
        smoke.resolve_binary(tmp_path, "win32")
    assert "winos-api-portable-windows.zip" in str(exc.value)


def test_wrong_platform_binary_is_not_accepted(smoke, tmp_path):
    """An ELF sitting in dist/ must not satisfy a Windows artifact check."""
    (tmp_path / "winos-api").write_bytes(b"ELF")
    with pytest.raises(smoke.SmokeError):
        smoke.resolve_binary(tmp_path, "win32")


def test_verify_version_accepts_exact_match(smoke):
    assert smoke.verify_version("1.0.0\n", "1.0.0") == "1.0.0"


def test_verify_version_rejects_mismatch(smoke):
    """A binary reporting a different version than the source tree is a stale build."""
    with pytest.raises(smoke.SmokeError) as exc:
        smoke.verify_version("0.9.0\n", "1.0.0")
    assert "version mismatch" in str(exc.value)


def test_verify_version_rejects_empty_output(smoke):
    """Ran but printed nothing = broken entry point, not a pass."""
    with pytest.raises(smoke.SmokeError):
        smoke.verify_version("   \n", "1.0.0")


def test_free_port_is_actually_bindable(smoke):
    port = smoke.free_port()
    assert 1024 < port < 65536
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


def test_port_is_open_detects_listener_and_silence(smoke):
    """port_is_open backs the orphan-process check; both directions must be right."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        assert smoke.port_is_open(port) is True
    assert smoke.port_is_open(port) is False


def test_wait_for_port_release_reports_a_live_listener(smoke):
    """A socket still accepting connections must NOT be reported as released.

    This is the orphan check. On Windows a PyInstaller onefile binary is two
    processes and killing only the bootloader leaves the child serving; if this
    returned True with a listener up, that orphan would ship unnoticed.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        # Backlog must exceed the number of probes: this listener never accepts,
        # and a full accept queue would start refusing connections, making a live
        # port look released.
        listener.listen(128)
        port = listener.getsockname()[1]
        assert smoke.wait_for_port_release(port, timeout=1.5) is False


def test_wait_for_port_release_returns_true_when_socket_is_down(smoke):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
    assert smoke.wait_for_port_release(port, timeout=5.0) is True


def test_wait_for_health_reports_child_output_on_early_exit(smoke):
    """When the frozen binary dies on launch, the error must carry its output.

    That output is the ImportError naming the hidden import PyInstaller dropped —
    without it, a maintainer sees only "did not answer" and learns nothing.
    """

    class DeadProcess:
        returncode = 3

        def __init__(self):
            self.stdout = _FakeStdout("ModuleNotFoundError: No module named 'comtypes'")

        def poll(self):
            return self.returncode

    class _FakeStdout:
        def __init__(self, text):
            self._text = text

        def read(self):
            return self._text

    with pytest.raises(smoke.SmokeError) as exc:
        smoke.wait_for_health(1, DeadProcess(), timeout=5.0)
    message = str(exc.value)
    assert "exited with code 3" in message
    assert "comtypes" in message
