"""Hard tests for installer_smoke logic.

The Windows lifecycle itself can only run on a Windows runner, but the checks
that decide PASS/FAIL are pure and testable anywhere — and they are the part
that must never silently accept a broken install.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def ism():
    """Load installer_smoke by path so scripts/ need not be a package."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "installer_smoke.py"
    spec = importlib.util.spec_from_file_location("installer_smoke", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _install(tmp_path: Path, *, key: str = "deadbeef") -> Path:
    """A directory shaped like a successful install."""
    d = tmp_path / "WinOsApi"
    d.mkdir()
    (d / "winos-api.exe").write_bytes(b"MZ")
    (d / "api_key.txt").write_text(key, encoding="utf-8")
    service = d / "service"
    service.mkdir()
    (service / "nssm.exe").write_bytes(b"MZ")
    return d


def test_find_setup_picks_the_installer(ism, tmp_path):
    (tmp_path / "WinOsApi-Setup-1.0.0.exe").write_bytes(b"MZ")
    assert ism.find_setup(tmp_path).name == "WinOsApi-Setup-1.0.0.exe"


def test_find_setup_without_installer_raises(ism, tmp_path):
    """No Setup produced must fail loudly, never pass quietly."""
    (tmp_path / "checksums.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ism.InstallerSmokeError) as exc:
        ism.find_setup(tmp_path)
    assert "checksums.txt" in str(exc.value)


def test_find_setup_missing_output_dir_raises(ism, tmp_path):
    with pytest.raises(ism.InstallerSmokeError):
        ism.find_setup(tmp_path / "nope")


def test_installed_layout_accepts_a_complete_install(ism, tmp_path):
    ism.verify_installed_layout(_install(tmp_path))


def test_installed_layout_rejects_missing_binary(ism, tmp_path):
    d = _install(tmp_path)
    (d / "winos-api.exe").unlink()
    with pytest.raises(ism.InstallerSmokeError) as exc:
        ism.verify_installed_layout(d)
    assert "winos-api.exe" in str(exc.value)


def test_installed_layout_rejects_missing_service_scripts(ism, tmp_path):
    d = _install(tmp_path)
    (d / "service" / "nssm.exe").unlink()
    (d / "service").rmdir()
    with pytest.raises(ism.InstallerSmokeError):
        ism.verify_installed_layout(d)


def test_installed_layout_rejects_empty_api_key(ism, tmp_path):
    """The .iss generates api_key.txt at ssPostInstall; an empty one is a broken install."""
    d = _install(tmp_path, key="   \n")
    with pytest.raises(ism.InstallerSmokeError) as exc:
        ism.verify_installed_layout(d)
    assert "empty" in str(exc.value)


def test_installed_layout_rejects_absent_install_dir(ism, tmp_path):
    with pytest.raises(ism.InstallerSmokeError) as exc:
        ism.verify_installed_layout(tmp_path / "never-created")
    assert "was not created" in str(exc.value)


def test_removal_accepts_a_clean_uninstall(ism, tmp_path):
    d = _install(tmp_path)
    for child in sorted(d.rglob("*"), reverse=True):
        child.rmdir() if child.is_dir() else child.unlink()
    d.rmdir()
    ism.verify_removed(d)


def test_removal_rejects_a_leftover_binary(ism, tmp_path):
    """A surviving EXE after uninstall is exactly the failure worth catching."""
    d = _install(tmp_path)
    with pytest.raises(ism.InstallerSmokeError) as exc:
        ism.verify_removed(d)
    assert "left the binary behind" in str(exc.value)


def test_removal_rejects_leftover_files(ism, tmp_path):
    d = _install(tmp_path)
    (d / "winos-api.exe").unlink()
    (d / "service" / "nssm.exe").unlink()
    (d / "service").rmdir()
    with pytest.raises(ism.InstallerSmokeError) as exc:
        ism.verify_removed(d)
    assert "api_key.txt" in str(exc.value)


def test_uninstaller_path_is_inno_convention(ism, tmp_path):
    assert ism.uninstaller_path(tmp_path).name == "unins000.exe"


def test_wait_for_returns_once_the_check_passes(ism):
    """Polling must accept a condition that becomes true a moment later."""
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ism.InstallerSmokeError("not yet")

    ism.wait_for(check, timeout=10, what="eventual success")
    assert calls["n"] == 2


def test_wait_for_still_fails_on_a_condition_that_never_holds(ism):
    """Polling must not soften the assertion: never true is still a failure.

    If this regressed into a silent pass, an install that produced nothing would
    be reported as successful — the exact outcome the smoke exists to prevent.
    """
    def check():
        raise ism.InstallerSmokeError("install dir was not created")

    with pytest.raises(ism.InstallerSmokeError) as exc:
        ism.wait_for(check, timeout=1.0, what="installed layout")
    assert "not satisfied within" in str(exc.value)
    assert "install dir was not created" in str(exc.value)


def test_wait_or_kill_tree_reports_a_process_that_exits_on_its_own(ism):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    assert ism.wait_or_kill_tree(proc, grace=30) is True


def test_wait_or_kill_tree_kills_and_reports_a_process_that_will_not_exit(ism):
    """A non-exiting installer must be killed AND reported, never silently accepted.

    Returning True here would hide the very defect this smoke observed on CI:
    Setup.exe finishing the install and then hanging forever.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert ism.wait_or_kill_tree(proc, grace=1.0) is False
        assert proc.poll() is not None, "the process tree must actually be dead"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_service_lifecycle_runs_the_dedicated_hard_smoke(ism, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ism, "_run", lambda cmd, what, **kwargs: calls.append((cmd, what)))

    ism.run_windows_service_lifecycle(tmp_path)

    cmd, what = calls[0]
    assert Path(cmd[1]).name == "windows_service_smoke.py"
    assert cmd[-2:] == ["--install-dir", str(tmp_path)]
    assert "service lifecycle" in what


# ---------------------------------------------------------------------------
# The .iss contract: whatever [Code] creates, [UninstallDelete] must remove.
# Runnable anywhere — no Windows needed to catch the regression.
# ---------------------------------------------------------------------------
ISS = Path(__file__).resolve().parents[2] / "installer" / "inno" / "winos-api.iss"


def _iss_section(name: str) -> str:
    """Return the body of one .iss section.

    Anchored to the start of a line: a bare text search would happily match a
    section name mentioned inside a comment elsewhere in the file, and silently
    return the wrong body.
    """
    import re

    text = ISS.read_text(encoding="utf-8")
    header = re.search(rf"^\[{name}\]\s*$", text, re.MULTILINE)
    assert header, f"section [{name}] not found in {ISS.name}"
    rest = text[header.end() :]
    nxt = re.search(r"^\[[A-Za-z]+\]\s*$", rest, re.MULTILINE)
    return rest if nxt is None else rest[: nxt.start()]


def _uninstall_directives() -> str:
    """The [UninstallDelete] DIRECTIVES, with comments stripped.

    Comments must not count: the explanation above each entry mentions the very
    filename the entry removes, so a guard that searched the raw section would
    still pass after someone deleted the directive and left the comment — a test
    that reports success for a broken uninstall.
    """
    body = _iss_section("UninstallDelete")
    return "\n".join(
        line for line in body.splitlines() if line.strip() and not line.lstrip().startswith(";")
    )


def test_uninstall_removes_the_generated_api_key():
    """The API key must not survive uninstallation.

    api_key.txt is written by [Code] at ssPostInstall, so it is NOT in [Files]
    and Inno does not track it: without an explicit [UninstallDelete] entry the
    uninstaller leaves a credential for an OS-control API sitting on disk.
    Observed for real by installer_smoke: "uninstall left files: api_key.txt".
    """
    assert "api_key.txt" in _uninstall_directives()


def test_uninstall_removes_the_runtime_audit_log():
    """logs/ holds API key prefixes and executed command lines — clean it up too."""
    assert "logs" in _uninstall_directives()


def test_uninstall_removes_product_owned_runtime_directories():
    """The installed backend and service TEMP must leave no residue below {app}."""
    cleanup = _uninstall_directives()
    assert r'Name: "{app}\sandbox"' in cleanup
    assert r'Name: "{app}\tmp"' in cleanup


def test_every_code_generated_file_is_covered_by_uninstalldelete():
    """Generalised guard: a new file written from [Code] must also be removed.

    Catches the whole class of bug, not just today's instance — adding another
    SaveStringToFile in [Code] without an UninstallDelete entry fails here.
    """
    import re

    code = _iss_section("Code")
    generated = set(re.findall(r"ExpandConstant\('\{app\}\\([A-Za-z0-9_.-]+)'\)", code))
    assert generated, "expected [Code] to generate at least api_key.txt"
    cleanup = _uninstall_directives()
    uncovered = sorted(name for name in generated if name not in cleanup)
    assert not uncovered, f"files created by [Code] but never uninstalled: {uncovered}"
