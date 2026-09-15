"""H63-N028 — Manifest checksum affidabile (R44 R45 G05)."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

from windows_os_api.update.checksum_manifest import (
    ChecksumManifestError,
    sha256_file,
    verify_checksum_manifest,
    write_checksum_manifest,
)
from windows_os_api.update.manager import UpdateManager, UpdatePackage


@pytest.fixture
def bi_mod():
    path = Path(__file__).resolve().parents[2] / "scripts" / "build_installer.py"
    spec = importlib.util.spec_from_file_location("build_installer", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_h63_n028_regen_twice_identical_and_independent_verify(tmp_path: Path, bi_mod):
    a = tmp_path / "alpha.bin"
    b = tmp_path / "beta.bin"
    a.write_bytes(b"hello-alpha")
    b.write_bytes(b"hello-beta")
    out = tmp_path / "checksums.txt"

    bi_mod.checksums([a, b], out)
    first = out.read_text(encoding="utf-8")
    bi_mod.checksums([a, b, out], out)  # explicit self must be excluded
    second = out.read_text(encoding="utf-8")
    assert first == second
    assert "checksums.txt" not in second

    mapping = verify_checksum_manifest(out, root=tmp_path)
    assert mapping["alpha.bin"] == hashlib.sha256(b"hello-alpha").hexdigest()
    assert mapping["beta.bin"] == hashlib.sha256(b"hello-beta").hexdigest()
    assert bi_mod.verify_checksums(out, root=tmp_path) == 0


def test_h63_n028_duplicate_basename_fails(tmp_path: Path, bi_mod):
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    f1 = d1 / "same.bin"
    f2 = d2 / "same.bin"
    f1.write_bytes(b"ONE")
    f2.write_bytes(b"TWO")
    out = tmp_path / "checksums.txt"
    with pytest.raises(ChecksumManifestError, match="duplicate basename"):
        bi_mod.checksums([f1, f2], out)


def test_h63_n028_missing_file_fails(tmp_path: Path, bi_mod):
    a = tmp_path / "a.bin"
    a.write_bytes(b"A")
    missing = tmp_path / "gone.bin"
    out = tmp_path / "checksums.txt"
    with pytest.raises(ChecksumManifestError, match="missing"):
        bi_mod.checksums([a, missing], out)


def test_h63_n028_altered_file_fails_independent_verify(tmp_path: Path):
    a = tmp_path / "a.bin"
    a.write_bytes(b"clean")
    out = tmp_path / "checksums.txt"
    write_checksum_manifest([a], out, root=tmp_path)
    verify_checksum_manifest(out, root=tmp_path)
    a.write_bytes(b"TAMPERED")
    with pytest.raises(ChecksumManifestError, match="mismatch"):
        verify_checksum_manifest(out, root=tmp_path)


def test_h63_n028_self_hash_line_rejected_by_parser(tmp_path: Path):
    a = tmp_path / "a.bin"
    a.write_bytes(b"x")
    digest = sha256_file(a)
    bad = tmp_path / "checksums.txt"
    bad.write_text(f"{digest}  a.bin\n{'0'*64}  checksums.txt\n", encoding="utf-8")
    with pytest.raises(ChecksumManifestError, match="must not list itself"):
        verify_checksum_manifest(bad, root=tmp_path)


def test_h63_n028_update_manager_dir_package_manifest(tmp_path: Path):
    install = tmp_path / "install"
    backup = tmp_path / "backup"
    install.mkdir()
    (install / "app.txt").write_text("v1", encoding="utf-8")
    mgr = UpdateManager(install, backup)

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "app.txt").write_text("v2", encoding="utf-8")
    (pkg / "extra.bin").write_bytes(b"EXTRA")
    manifest = UpdateManager.write_package_manifest(pkg)
    assert manifest.name == "checksums.txt"
    assert "checksums.txt" not in manifest.read_text(encoding="utf-8")

    good = UpdatePackage(
        version="2.0.0",
        path=pkg,
        checksum_sha256=sha256_file(manifest),
    )
    assert mgr.verify(good) is True
    result = mgr.apply(good)
    assert result["ok"] is True
    assert (install / "app.txt").read_text(encoding="utf-8") == "v2"

    # Alter payload after manifest → verify fails; apply blocked
    (pkg / "app.txt").write_text("EVIL", encoding="utf-8")
    assert mgr.verify(good) is False
    blocked = mgr.apply(good)
    assert blocked["ok"] is False

    # Missing member listed in manifest → fail
    (pkg / "app.txt").write_text("v2", encoding="utf-8")
    # refresh manifest then delete a file
    manifest = UpdateManager.write_package_manifest(pkg)
    good2 = UpdatePackage(version="2.0.1", path=pkg, checksum_sha256=sha256_file(manifest))
    (pkg / "extra.bin").unlink()
    assert mgr.verify(good2) is False


def test_h63_n028_update_manager_single_file_still_works(tmp_path: Path):
    install = tmp_path / "install"
    backup = tmp_path / "backup"
    install.mkdir()
    (install / "app.txt").write_text("v1", encoding="utf-8")
    mgr = UpdateManager(install, backup)
    src = tmp_path / "release.bin"
    src.write_bytes(b"NEWCONTENT")
    good = UpdatePackage(version="2.0.0", path=src, checksum_sha256=sha256_file(src))
    assert mgr.verify(good) is True
    assert mgr.apply(good)["ok"] is True
    assert (install / "release.bin").read_bytes() == b"NEWCONTENT"
    bad = UpdatePackage(version="2.0.0", path=src, checksum_sha256="0" * 64)
    assert mgr.verify(bad) is False


def test_h63_n028_unique_relative_paths_with_root(tmp_path: Path):
    """With root=, same basename in different dirs get unique relative labels."""
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    f1 = d1 / "same.bin"
    f2 = d2 / "same.bin"
    f1.write_bytes(b"ONE")
    f2.write_bytes(b"TWO")
    out = tmp_path / "checksums.txt"
    write_checksum_manifest([f1, f2], out, root=tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "d1/same.bin" in text
    assert "d2/same.bin" in text
    mapping = verify_checksum_manifest(out, root=tmp_path)
    assert mapping["d1/same.bin"] == hashlib.sha256(b"ONE").hexdigest()
    assert mapping["d2/same.bin"] == hashlib.sha256(b"TWO").hexdigest()


def test_h63_n028_sha256sums_may_list_platform_checksums(tmp_path: Path, bi_mod):
    """Top-level SHA256SUMS.txt may include checksums.txt as an artifact (not self)."""
    artifact = tmp_path / "app.bin"
    artifact.write_bytes(b"APP")
    platform = tmp_path / "checksums.txt"
    platform.write_text(
        f"{hashlib.sha256(b'APP').hexdigest()}  app.bin\n", encoding="utf-8"
    )
    top = tmp_path / "SHA256SUMS.txt"
    bi_mod.checksums([artifact, platform, top], top)  # top excluded
    body = top.read_text(encoding="utf-8")
    assert "SHA256SUMS.txt" not in body
    assert "checksums.txt" in body
    assert "app.bin" in body
    mapping = verify_checksum_manifest(top, root=tmp_path)
    assert "checksums.txt" in mapping
    assert "app.bin" in mapping
