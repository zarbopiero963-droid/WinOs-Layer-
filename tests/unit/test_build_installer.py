"""Hard tests for build_installer validate + checksum pure logic."""
from __future__ import annotations

import hashlib
import importlib.util
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
    result = bi_mod.validate(verbose=False)
    assert result["ok"] is False
    assert result["errors"]
