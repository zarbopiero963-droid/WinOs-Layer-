"""N049 / H63-N049 — filesystem + terminal boundaries (Q04).

Coverage refs: R12 R21 W049 L049 G06 G08 G13 G20.
Installed W/L: MANUAL_ONLY (never claim PASS here).

Contratto: sandbox uniforme read/write/delete/hash/stat; handle-safe contro
symlink/reparse/TOCTOU; terminale argv/allowlist invarianti. UNC/traversal/
encoded-shell negati senza toccare un sentinel.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

import pytest

from windows_os_api.backends.factory import get_backend, reset_backend
from windows_os_api.core.runtime.config import get_settings
from windows_os_api.os.filesystem import paths as fspaths
from windows_os_api.os.filesystem import service as fs
from windows_os_api.os.terminal import allowlist as al


@pytest.fixture
def sandboxed(monkeypatch, tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setenv("WINOS_BACKEND", "linux")
    monkeypatch.setenv("WINOS_ALLOW_FAKE_FALLBACK", "false")
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(root))
    get_settings.cache_clear()
    reset_backend()
    # Force backend to use our root even if env mapping differs.
    b = get_backend()
    b.sandbox = root.resolve()
    yield root.resolve()
    reset_backend()
    get_settings.cache_clear()


def test_write_read_stat_hash_roundtrip(sandboxed):
    assert fs.write_file("a.txt", "payload-n049")["ok"] is True
    read = fs.read_file("a.txt")
    assert read["ok"] is True
    assert read["text"] == "payload-n049"
    st = fs.stat_file("a.txt")
    assert st["ok"] is True and st["is_file"] is True and st["size"] == 12
    digest = fs.hash_file("a.txt")
    assert digest["ok"] is True
    assert digest["hex"] == hashlib.sha256(b"payload-n049").hexdigest()


def test_traversal_and_unc_are_blocked_without_touching_sentinel(sandboxed):
    sentinel = sandboxed / "sentinel.txt"
    sentinel.write_text("UNTOUCHED", encoding="utf-8")
    for bad in (
        "../etc/passwd",
        "foo/../../etc/passwd",
        "//etc/passwd",
        "\\\\server\\share\\x",
        "..\\..\\windows\\win.ini",
    ):
        out = fs.read_file(bad)
        assert out.get("ok") is False, bad
        assert out.get("code") in {
            fspaths.PATH_TRAVERSAL,
            fspaths.PATH_OUTSIDE_SANDBOX,
        }, out
    assert sentinel.read_text(encoding="utf-8") == "UNTOUCHED"


def test_symlink_leaf_write_is_refused_and_outside_untouched(sandboxed):
    outside = Path("/tmp/n049_unit_victim.txt")
    outside.write_text("SAFE", encoding="utf-8")
    link = sandboxed / "escape"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(outside)
    try:
        out = fs.write_file("escape", "PWNED")
        assert out["ok"] is False
        assert out["code"] == fspaths.PATH_SYMLINK_REFUSED
        assert outside.read_text(encoding="utf-8") == "SAFE"
    finally:
        link.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)


def test_toctou_symlink_swap_cannot_escape_sandbox(sandboxed):
    """Il falso successo di Phase 0: resolve ok → swap → write fuori."""
    outside = Path("/tmp/n049_unit_toctou.txt")
    outside.write_text("ORIGINAL", encoding="utf-8")
    name = "race"
    final = sandboxed / name
    final.write_text("inside", encoding="utf-8")
    box: dict = {}

    def writer():
        time.sleep(0.001)
        box["w"] = fs.write_file(name, "FROM_API")

    def swapper():
        time.sleep(0.0005)
        final.unlink()
        final.symlink_to(outside)
        box["sw"] = True

    t1 = threading.Thread(target=writer)
    t2 = threading.Thread(target=swapper)
    t1.start(); t2.start(); t1.join(); t2.join()
    try:
        assert box.get("sw") is True
        assert box["w"]["ok"] is False
        assert box["w"]["code"] == fspaths.PATH_SYMLINK_REFUSED
        assert outside.read_text(encoding="utf-8") == "ORIGINAL"
    finally:
        if final.is_symlink() or final.exists():
            final.unlink()
        outside.unlink(missing_ok=True)


def test_partial_failure_in_dedicated_root_leaves_recovery_path(sandboxed):
    """Recovery: dopo un rifiuto la root resta usabile e coerente."""
    assert fs.write_file("ok1.txt", "one")["ok"] is True
    denied = fs.write_file("../outside.txt", "nope")
    assert denied["ok"] is False
    # La root dedicata resta intatta e scrivibile.
    assert (sandboxed / "ok1.txt").read_text(encoding="utf-8") == "one"
    assert fs.write_file("ok2.txt", "two")["ok"] is True
    assert fs.read_file("ok2.txt")["text"] == "two"


def test_delete_refuses_symlink_without_removing_target(sandboxed):
    outside = Path("/tmp/n049_unit_del.txt")
    outside.write_text("KEEP", encoding="utf-8")
    link = sandboxed / "dellink"
    link.symlink_to(outside)
    try:
        out = fs.delete_file("dellink")
        assert out["ok"] is False
        assert out["code"] == fspaths.PATH_SYMLINK_REFUSED
        assert outside.read_text(encoding="utf-8") == "KEEP"
        assert link.is_symlink()
    finally:
        link.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)


# ----- terminal invariants (encoded-shell / argv) -----


@pytest.mark.parametrize(
    "cmd",
    [
        "bash -c id",
        "sh -c 'id'",
        "python3 -c 'print(1)'",
        "whoami; id",
        "whoami && id",
        "$(whoami)",
        "`whoami`",
        "curl http://example",
        "echo pwned",
    ],
)
def test_terminal_encoded_shell_and_unregistered_are_rejected(cmd):
    with pytest.raises(al.CommandRejected):
        al.resolve(cmd)


def test_terminal_allowlisted_command_resolves_to_argv_without_shell():
    argv = al.resolve("whoami")
    assert isinstance(argv, list)
    assert len(argv) >= 1
    assert os.path.isabs(argv[0])
    assert argv[0].endswith("whoami") or Path(argv[0]).name == "whoami"
