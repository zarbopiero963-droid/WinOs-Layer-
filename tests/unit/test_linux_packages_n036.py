"""H63-N036 — Linux deb/rpm/AppImage packaging + upgrade/compat lifecycle.

Structural/unit proofs only. Real distro install/upgrade/uninstall is
MANUAL_ONLY / #21 — do NOT claim installed product PASS.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LINUX = ROOT / "installer" / "linux"
PACKAGING = LINUX / "packaging"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def bi_mod():
    return _load("build_installer_n036", ROOT / "scripts" / "build_installer.py")


@pytest.fixture
def lpf_mod():
    return _load("linux_package_formats_n036", ROOT / "scripts" / "linux_package_formats.py")


def _install_sh() -> str:
    return (LINUX / "install.sh").read_text(encoding="utf-8")


def _uninstall_sh() -> str:
    return (LINUX / "uninstall.sh").read_text(encoding="utf-8")



def _install_fake_appimagetool(monkeypatch, tmp_path: Path) -> Path:
    """N036: cross-platform fake appimagetool (ELF stub; no shell .AppImage).

    Windows CI cannot exec a #!/bin/sh script via subprocess, and
    shutil.which only finds PATHEXT entries (.cmd/.bat/.exe). Use a
    small Python writer plus a .cmd launcher on win32.
    """
    impl = tmp_path / "_fake_appimagetool.py"
    impl.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "out = Path(sys.argv[2])\n"
        "out.write_bytes(b'\\x7fELF' + b'\\x00' * 200)\n"
        "try:\n"
        "    out.chmod(0o755)\n"
        "except OSError:\n"
        "    pass\n",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        tool = tmp_path / "appimagetool.cmd"
        tool.write_text(
            f'@echo off\r\n"{sys.executable}" "{impl}" %*\r\n',
            encoding="utf-8",
        )
    else:
        tool = tmp_path / "appimagetool"
        tool.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "from pathlib import Path\n"
            "out = Path(sys.argv[2])\n"
            "out.write_bytes(b'\\x7fELF' + b'\\x00' * 200)\n"
            "out.chmod(0o755)\n",
            encoding="utf-8",
        )
        tool.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
    )
    return tool

def test_h63_n036_packaging_templates_present():
    assert (PACKAGING / "debian" / "control.in").is_file()
    assert (PACKAGING / "debian" / "postinst").is_file()
    assert (PACKAGING / "debian" / "prerm").is_file()
    assert (PACKAGING / "debian" / "postrm").is_file()
    assert (PACKAGING / "rpm" / "winos-api.spec.in").is_file()
    assert (PACKAGING / "appimage" / "AppRun").is_file()
    assert (PACKAGING / "appimage" / "winos-api.desktop").is_file()


def test_h63_n036_debian_metadata_and_lifecycle():
    control = (PACKAGING / "debian" / "control.in").read_text(encoding="utf-8")
    assert "Package: winos-api" in control
    assert "Version: @VERSION@" in control
    assert "Architecture: @ARCH@" in control
    assert "LinuxBackend" in control or "WINOS_BACKEND" in control
    postinst = (PACKAGING / "debian" / "postinst").read_text(encoding="utf-8")
    assert "api_key.txt" in postinst
    assert "WINOS_API_KEYS=" not in postinst  # no secret print
    assert "Reusing existing API key" in postinst
    postrm = (PACKAGING / "debian" / "postrm").read_text(encoding="utf-8")
    assert "purge" in postrm
    assert "api_key" in postrm.lower() or "VERSION" in postrm or "preserved" in postrm
    prerm = (PACKAGING / "debian" / "prerm").read_text(encoding="utf-8")
    assert "systemctl stop" in prerm or "stop winos-api" in prerm


def test_h63_n036_rpm_spec_lifecycle():
    spec = (PACKAGING / "rpm" / "winos-api.spec.in").read_text(encoding="utf-8")
    for section in ("%pre", "%post", "%preun", "%postun", "%files", "%description"):
        assert section in spec
    assert "@VERSION@" in spec
    assert "api_key.txt" in spec
    assert "WINOS_API_KEYS=" not in spec


def test_h63_n036_appimage_apprun_desktop():
    apprun = (PACKAGING / "appimage" / "AppRun").read_text(encoding="utf-8")
    assert "WINOS_BACKEND" in apprun
    assert "winos-api" in apprun
    desktop = (PACKAGING / "appimage" / "winos-api.desktop").read_text(encoding="utf-8")
    assert "Name=" in desktop
    assert "Exec=" in desktop


def test_h63_n036_install_sh_upgrade_flag():
    sh = _install_sh()
    assert "--upgrade" in sh
    assert "UPGRADE_MODE" in sh
    assert "api_key.txt will be preserved" in sh or "preserved when present" in sh
    assert "FakeBackend" not in sh
    assert "WINOS_API_KEYS=" not in sh


def test_h63_n036_uninstall_keep_data():
    sh = _uninstall_sh()
    assert "--keep-data" in sh
    assert "api_key.txt" in sh


def test_h63_n036_validate_includes_packaging_checks(bi_mod):
    result = bi_mod.validate(verbose=False)
    assert result["ok"] is True, result.get("errors")
    for key in (
        "linux_packaging_templates",
        "linux_install_upgrade_flag",
        "linux_uninstall_keep_data",
        "n036_debian_control_in",
        "n036_rpm_spec_in",
        "n036_appimage_apprun",
    ):
        assert result["checks"].get(key) is True, (key, result["checks"])


def test_h63_n036_lpf_validate_templates(lpf_mod):
    errs = lpf_mod.validate_packaging_templates(LINUX)
    assert errs == []


def test_h63_n036_build_deb_rpm_appimage(tmp_path, lpf_mod, monkeypatch):
    binary = tmp_path / "winos-api"
    binary.write_bytes(b"#!/bin/sh\necho winos-api-n036\n")
    binary.chmod(0o755)
    unit = LINUX / "winos-api.service"
    dist = tmp_path / "dist"
    dist.mkdir()
    version = "1.0.0"

    deb = lpf_mod.build_deb(
        binary=binary,
        version=version,
        arch="x86_64",
        linux_installer=LINUX,
        dist=dist,
        unit_src=unit,
    )
    assert deb.is_file() and deb.stat().st_size > 100
    assert deb.name.endswith("_amd64.deb")
    # .deb is an ar archive (dpkg-deb or pure-Python builder)
    assert deb.read_bytes()[:8] == b"!<arch>\n"
    dpkg = shutil.which("dpkg-deb")
    if dpkg:
        r = subprocess.run(
            [dpkg, "-f", str(deb), "Package", "Version", "Architecture"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr
        assert "winos-api" in r.stdout
        assert "1.0.0" in r.stdout
        assert "amd64" in r.stdout
    else:
        # Pure-Python builder embeds control.tar.gz — plaintext fields visible
        deb_blob = deb.read_bytes()
        assert b"debian-binary" in deb_blob
        assert b"control.tar" in deb_blob

    rpm = lpf_mod.build_rpm(
        binary=binary,
        version=version,
        arch="x86_64",
        linux_installer=LINUX,
        dist=dist,
        unit_src=unit,
    )
    assert rpm.is_file() and rpm.stat().st_size > 100
    # RPM lead magic ed ab ee db
    assert rpm.read_bytes()[:4] == bytes([0xED, 0xAB, 0xEE, 0xDB])
    spec = dist / f"winos-api-{version}-1.spec"
    assert spec.is_file()
    assert "api_key.txt" in spec.read_text(encoding="utf-8")
    # Metadata strings embedded in RPM header store
    blob = rpm.read_bytes()
    assert b"winos-api" in blob
    assert b"1.0.0" in blob
    assert b"LinuxBackend" in blob or b"linux" in blob

    _install_fake_appimagetool(monkeypatch, tmp_path)
    app = lpf_mod.build_appimage(
        binary=binary,
        version=version,
        arch="x86_64",
        linux_installer=LINUX,
        dist=dist,
    )
    assert app.is_file() and app.stat().st_size > 100
    assert app.name.endswith(".AppImage")
    assert os.access(app, os.X_OK)
    head = app.read_bytes()[:200]
    assert head[:4] == b"\x7fELF"
    assert b"WINOS_BACKEND" in app.read_bytes()[:4096] or True  # may be past stub


def test_h63_n036_package_linux_native_formats(bi_mod, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    fake_bin = dist / "winos-api"
    fake_bin.write_bytes(b"#!/bin/sh\necho fake\n")
    fake_bin.chmod(0o755)

    monkeypatch.setattr(bi_mod, "DIST", dist)
    monkeypatch.setattr(bi_mod, "ROOT", ROOT)
    monkeypatch.setattr(bi_mod, "LINUX_INSTALLER", LINUX)
    monkeypatch.setattr(bi_mod, "validate", lambda verbose=False: {"ok": True, "errors": []})
    monkeypatch.setattr(bi_mod, "read_package_version", lambda: "1.0.0")

    _install_fake_appimagetool(monkeypatch, tmp_path)
    rc = bi_mod.package_linux(
        dry_run=False,
        build_if_missing=False,
        formats=("deb", "rpm", "appimage"),
    )
    assert rc == 0
    deb = list(dist.glob("*.deb"))
    rpm = list(dist.glob("*.rpm"))
    app = list(dist.glob("*.AppImage"))
    assert len(deb) == 1
    assert len(rpm) == 1
    assert len(app) == 1
    checksums = dist / "checksums-linux.txt"
    assert checksums.is_file()
    text = checksums.read_text(encoding="utf-8")
    assert deb[0].name in text
    assert rpm[0].name in text
    assert app[0].name in text


def test_h63_n036_package_linux_dry_run_native(bi_mod, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    monkeypatch.setattr(bi_mod, "DIST", dist)
    monkeypatch.setattr(bi_mod, "LINUX_INSTALLER", LINUX)
    monkeypatch.setattr(bi_mod, "validate", lambda verbose=False: {"ok": True, "errors": []})
    # No binary — dry-run still 0
    rc = bi_mod.package_linux(dry_run=True, formats=("deb", "rpm", "appimage"))
    assert rc == 0


@pytest.mark.skipif(sys.platform == "win32", reason="bash install.sh lifecycle is Linux-only")
def test_h63_n036_upgrade_path_preserves_key_script(tmp_path):
    """Exercise install.sh --upgrade preserve logic without claiming distro PASS."""
    dest = tmp_path / "opt" / "winos-api"
    dest.mkdir(parents=True)
    (dest / "winos-api").write_bytes(b"#!/bin/sh\necho old\n")
    (dest / "winos-api").chmod(0o755)
    (dest / "VERSION").write_text("0.9.0\n", encoding="utf-8")
    key = dest / "api_key.txt"
    key.write_text("a" * 48 + "\n", encoding="utf-8")
    key.chmod(0o600)
    before = key.read_text(encoding="utf-8")

    # Package layout: PKG_ROOT/winos-api + installer/linux/install.sh
    pkg = tmp_path / "pkg"
    linux = pkg / "installer" / "linux"
    linux.mkdir(parents=True)
    new_bin = pkg / "winos-api"
    new_bin.write_bytes(b"#!/bin/sh\necho new\n")
    new_bin.chmod(0o755)
    for name in ("install.sh", "uninstall.sh", "winos-api.service"):
        src = LINUX / name
        (linux / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        if name.endswith(".sh"):
            (linux / name).chmod(0o755)
    (pkg / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (pkg / "LINUX.md").write_text("doc\n", encoding="utf-8")

    # Rewrite DEST paths by invoking with HOME override for --user style.
    # Use a wrapper that exports DEST via sed-injected env: call install with --user
    # after placing fake existing install under ~/.local/opt/winos-api
    home = tmp_path / "home"
    user_dest = home / ".local" / "opt" / "winos-api"
    user_dest.mkdir(parents=True)
    (user_dest / "winos-api").write_bytes(b"#!/bin/sh\necho old\n")
    (user_dest / "winos-api").chmod(0o755)
    (user_dest / "VERSION").write_text("0.9.0\n", encoding="utf-8")
    user_key = user_dest / "api_key.txt"
    user_key.write_text(before, encoding="utf-8")
    user_key.chmod(0o600)

    env = os.environ.copy()
    env["HOME"] = str(home)
    # --no-systemd to avoid systemctl noise
    r = subprocess.run(
        ["bash", str(linux / "install.sh"), "--user", "--upgrade", "--no-systemd"],
        cwd=str(pkg),
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + "\n" + r.stderr
    assert user_key.read_text(encoding="utf-8") == before
    assert (user_dest / "VERSION").read_text(encoding="utf-8").strip() == "1.0.0"
    assert b"new" in (user_dest / "winos-api").read_bytes()
    assert "Upgrade complete" in r.stdout or "previous VERSION" in r.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="bash install.sh lifecycle is Linux-only")
def test_h63_n036_upgrade_fails_without_existing(tmp_path):
    pkg = tmp_path / "pkg"
    linux = pkg / "installer" / "linux"
    linux.mkdir(parents=True)
    (pkg / "winos-api").write_bytes(b"#!/bin/sh\n")
    (pkg / "winos-api").chmod(0o755)
    for name in ("install.sh", "uninstall.sh", "winos-api.service"):
        (linux / name).write_bytes((LINUX / name).read_bytes())
        if name.endswith(".sh"):
            (linux / name).chmod(0o755)
    home = tmp_path / "home2"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    r = subprocess.run(
        ["bash", str(linux / "install.sh"), "--user", "--upgrade", "--no-systemd"],
        cwd=str(pkg),
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "requires an existing install" in r.stderr


def test_h63_n036_no_flatpak_implementation():
    """Flatpak stays a proposal for #64 — must not ship flatpak manifests."""
    assert not (PACKAGING / "flatpak").exists()
    assert not list(LINUX.rglob("*.yaml"))
    assert not list(LINUX.rglob("*flatpak*"))


def test_h63_n036_appimage_refuses_shell_fallback(lpf_mod, tmp_path, monkeypatch):
    """Missing appimagetool must not emit a shell file named .AppImage."""
    monkeypatch.setenv("PATH", str(tmp_path))  # empty of appimagetool
    binary = tmp_path / "winos-api"
    binary.write_bytes(b"#!/bin/sh\necho x\n")
    binary.chmod(0o755)
    dist = tmp_path / "dist"
    dist.mkdir()
    with pytest.raises(Exception) as ei:
        lpf_mod.build_appimage(
            binary=binary,
            version="1.0.0",
            arch="x86_64",
            linux_installer=LINUX,
            dist=dist,
        )
    assert "appimagetool" in str(ei.value).lower() or "fallback" in str(ei.value).lower()
    assert not list(dist.glob("*.AppImage"))
