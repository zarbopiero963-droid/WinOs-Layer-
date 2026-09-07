"""Hard tests for build_installer validate + checksum + package-linux logic."""
from __future__ import annotations

import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def bi_mod():
    """Load build_installer by path so scripts/ need not be a package."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "build_installer.py"
    spec = importlib.util.spec_from_file_location("build_installer", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_validate_passes_on_repo(bi_mod):
    result = bi_mod.validate(verbose=False)
    assert result["ok"] is True, result.get("errors")
    assert result["service_name"] == "WindowsOSLayerService"
    assert result["entry_point"] == "winos-api"
    assert result["checks"].get("pyinstaller_spec") is True
    assert result["checks"].get("inno_iss") is True
    assert result["checks"].get("service_name_module") is True
    assert result["checks"].get("linux_service") is True
    assert result["checks"].get("linux_install_sh") is True
    assert result["checks"].get("linux_uninstall_sh") is True
    assert result.get("version")


def test_checksums_generation(bi_mod, tmp_path):
    a = tmp_path / "alpha.bin"
    b = tmp_path / "beta.bin"
    a.write_bytes(b"hello-alpha")
    b.write_bytes(b"hello-beta")
    out = tmp_path / "checksums.txt"
    written = bi_mod.checksums([a, b], out)
    assert written == out
    text = out.read_text(encoding="utf-8")
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    assert len(lines) == 2
    for path in (a, b):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert any(ln.startswith(digest) and path.name in ln for ln in lines)


def test_sha256_file(bi_mod, tmp_path):
    f = tmp_path / "x.dat"
    data = b"abc" * 1000
    f.write_bytes(data)
    assert bi_mod.sha256_file(f) == hashlib.sha256(data).hexdigest()


def test_validate_detects_missing_files(bi_mod, tmp_path, monkeypatch):
    fake_root = tmp_path / "empty"
    fake_root.mkdir()
    monkeypatch.setattr(bi_mod, "ROOT", fake_root)
    monkeypatch.setattr(bi_mod, "SPEC", fake_root / "no.spec")
    monkeypatch.setattr(bi_mod, "ISS", fake_root / "no.iss")
    monkeypatch.setattr(bi_mod, "SERVICE_SCRIPTS", fake_root / "svc")
    monkeypatch.setattr(bi_mod, "LINUX_INSTALLER", fake_root / "linux")
    result = bi_mod.validate(verbose=False)
    assert result["ok"] is False
    assert result["errors"]


def test_package_linux_dry_run_without_binary(bi_mod, tmp_path, monkeypatch):
    """Dry-run succeeds even when binary is absent (documents intent)."""
    dist = tmp_path / "dist"
    dist.mkdir()
    linux = tmp_path / "installer" / "linux"
    linux.mkdir(parents=True)
    (linux / "winos-api.service").write_text("[Unit]\nDescription=x\n", encoding="utf-8")
    (linux / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (linux / "uninstall.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (linux / "LINUX.md").write_text("linux\n", encoding="utf-8")

    monkeypatch.setattr(bi_mod, "DIST", dist)
    monkeypatch.setattr(bi_mod, "LINUX_INSTALLER", linux)
    # Keep real ROOT validate happy by not changing ROOT — instead stub validate
    monkeypatch.setattr(bi_mod, "validate", lambda verbose=False: {"ok": True, "errors": []})

    rc = bi_mod.package_linux(dry_run=True, build_if_missing=False)
    assert rc == 0


def test_package_linux_with_fake_binary(bi_mod, tmp_path, monkeypatch):
    """Create zip from a fake binary + linux installer files."""
    dist = tmp_path / "dist"
    dist.mkdir()
    fake_bin = dist / "winos-api"
    fake_bin.write_bytes(b"#!/bin/sh\necho fake-winos-api\n")
    fake_bin.chmod(0o755)

    linux = tmp_path / "installer" / "linux"
    linux.mkdir(parents=True)
    (linux / "winos-api.service").write_text(
        "# FakeBackend\nEnvironment=WINOS_BACKEND=fake\nExecStart=/opt/winos-api/winos-api serve --host 127.0.0.1 --port 8765\n",
        encoding="utf-8",
    )
    (linux / "install.sh").write_text("#!/bin/sh\necho install\n", encoding="utf-8")
    (linux / "uninstall.sh").write_text("#!/bin/sh\necho uninstall\n", encoding="utf-8")
    (linux / "LINUX.md").write_text("Linux portable — FakeBackend\n", encoding="utf-8")

    # Minimal installer README for copy
    installer_readme = tmp_path / "installer" / "README.md"
    installer_readme.write_text("readme\n", encoding="utf-8")

    monkeypatch.setattr(bi_mod, "DIST", dist)
    monkeypatch.setattr(bi_mod, "LINUX_INSTALLER", linux)
    monkeypatch.setattr(bi_mod, "ROOT", tmp_path)
    monkeypatch.setattr(bi_mod, "validate", lambda verbose=False: {"ok": True, "errors": []})
    monkeypatch.setattr(bi_mod, "read_package_version", lambda: "9.9.9-test")

    rc = bi_mod.package_linux(dry_run=False, build_if_missing=False, formats=("zip",))
    assert rc == 0

    zip_path = dist / "winos-api-portable-linux.zip"
    assert zip_path.is_file()
    checksums = dist / "checksums-linux.txt"
    assert checksums.is_file()
    assert "winos-api-portable-linux.zip" in checksums.read_text(encoding="utf-8")

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
    assert any(n.endswith("winos-api") and "installer" not in n for n in names)
    assert any("installer/linux/install.sh" in n for n in names)
    assert any("installer/linux/winos-api.service" in n for n in names)
    assert any(n.endswith("VERSION") for n in names)
    # VERSION content
    version_members = [n for n in names if n.endswith("VERSION")]
    with zipfile.ZipFile(zip_path, "r") as zf:
        ver = zf.read(version_members[0]).decode("utf-8").strip()
    assert ver == "9.9.9-test"


def test_package_linux_refuses_when_validate_fails(bi_mod, monkeypatch):
    monkeypatch.setattr(
        bi_mod, "validate", lambda verbose=False: {"ok": False, "errors": ["boom"]}
    )
    rc = bi_mod.package_linux(dry_run=False)
    assert rc == 1


def test_read_package_version(bi_mod):
    v = bi_mod.read_package_version()
    assert isinstance(v, str) and len(v) > 0
    assert v[0].isdigit()
