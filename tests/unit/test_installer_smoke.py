"""Hard tests for installer_smoke logic.

The Windows lifecycle itself can only run on a Windows runner, but the checks
that decide PASS/FAIL are pure and testable anywhere — and they are the part
that must never silently accept a broken install.
"""
from __future__ import annotations

import importlib.util
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
    (d / "service").mkdir()
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
    for child in sorted(d.iterdir(), reverse=True):
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
    (d / "service").rmdir()
    with pytest.raises(ism.InstallerSmokeError) as exc:
        ism.verify_removed(d)
    assert "api_key.txt" in str(exc.value)


def test_uninstaller_path_is_inno_convention(ism, tmp_path):
    assert ism.uninstaller_path(tmp_path).name == "unins000.exe"
