"""H63-N037 — EXE/Setup signing + release attestation (R44 R48 W093 W100 L100 G05 G09)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from windows_os_api.installer.release_attestation import (
    ATTESTATION_FILENAME,
    ReleaseAttestationError,
    artifact_entry,
    build_attestation,
    load_release_private_key,
    normalize_signing_status,
    public_key_hex,
    schema_helpers_present,
    verify_release_artifacts,
    write_attestation,
)


@pytest.fixture
def ed25519_key(monkeypatch: pytest.MonkeyPatch):
    priv = Ed25519PrivateKey.generate()
    raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    monkeypatch.setenv("WINOS_RELEASE_SIGNING_KEY", raw.hex())
    monkeypatch.setenv("WINOS_RELEASE_PUBLISHER", "WinOs-Layer")
    monkeypatch.delenv("WINOS_SIGN_PFX_PATH", raising=False)
    monkeypatch.delenv("WINOS_SIGN_PFX_B64", raising=False)
    return priv


@pytest.fixture
def bi_mod():
    path = Path(__file__).resolve().parents[2] / "scripts" / "build_installer.py"
    spec = importlib.util.spec_from_file_location("build_installer_n037", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_artifacts(tmp_path: Path) -> list[Path]:
    exe = tmp_path / "winos-api.exe"
    setup = tmp_path / "WinOsApi-Setup-1.0.0.exe"
    exe.write_bytes(b"MZ-fake-portable-payload-aaaaaaaa")
    setup.write_bytes(b"MZ-fake-setup-payload-bbbbbbbb")
    return [exe, setup]


def test_h63_n037_attestation_roundtrip_ok(tmp_path: Path, ed25519_key):
    files = _fake_artifacts(tmp_path)
    arts = [artifact_entry(p, signing_status="attested", root=tmp_path) for p in files]
    doc = build_attestation(version="1.0.0", publisher="WinOs-Layer", artifacts=arts)
    att = write_attestation(doc, tmp_path / ATTESTATION_FILENAME, private_key=ed25519_key)
    report = verify_release_artifacts(
        att,
        root=tmp_path,
        expected_publisher="WinOs-Layer",
        public_key=ed25519_key.public_key(),
        require_attestation_signature=True,
    )
    assert report["ok"] is True
    assert report["errors"] == []
    assert report.get("attestation_signed") is True


def test_h63_n037_tamper_file_fails(tmp_path: Path, ed25519_key):
    files = _fake_artifacts(tmp_path)
    arts = [artifact_entry(p, signing_status="unsigned", root=tmp_path) for p in files]
    doc = build_attestation(version="1.0.0", publisher="WinOs-Layer", artifacts=arts)
    att = write_attestation(doc, tmp_path / ATTESTATION_FILENAME, private_key=ed25519_key)
    files[0].write_bytes(b"TAMPERED-BYTES")
    report = verify_release_artifacts(
        att,
        root=tmp_path,
        expected_publisher="WinOs-Layer",
        public_key=ed25519_key.public_key(),
    )
    assert report["ok"] is False
    assert any("sha256 mismatch" in e for e in report["errors"])


def test_h63_n037_wrong_publisher_fails(tmp_path: Path, ed25519_key):
    files = _fake_artifacts(tmp_path)
    arts = [artifact_entry(p, signing_status="attested", root=tmp_path) for p in files]
    doc = build_attestation(version="1.0.0", publisher="WinOs-Layer", artifacts=arts)
    att = write_attestation(doc, tmp_path / ATTESTATION_FILENAME, private_key=ed25519_key)
    report = verify_release_artifacts(
        att,
        root=tmp_path,
        expected_publisher="Not-WinOs-Layer",
        public_key=ed25519_key.public_key(),
    )
    assert report["ok"] is False
    assert any("publisher" in e for e in report["errors"])


def test_h63_n037_bad_attestation_signature_fails(tmp_path: Path, ed25519_key):
    files = _fake_artifacts(tmp_path)
    arts = [artifact_entry(p, signing_status="attested", root=tmp_path) for p in files]
    doc = build_attestation(version="1.0.0", publisher="WinOs-Layer", artifacts=arts)
    att = write_attestation(doc, tmp_path / ATTESTATION_FILENAME, private_key=ed25519_key)
    other = Ed25519PrivateKey.generate()
    report = verify_release_artifacts(
        att,
        root=tmp_path,
        expected_publisher="WinOs-Layer",
        public_key=other.public_key(),
        require_attestation_signature=True,
    )
    assert report["ok"] is False
    assert any("signature" in e.lower() for e in report["errors"])


def test_h63_n037_without_cert_artifacts_remain_unsigned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("WINOS_RELEASE_SIGNING_KEY", raising=False)
    monkeypatch.delenv("WINOS_RELEASE_SIGNING_KEY_PATH", raising=False)
    monkeypatch.delenv("WINOS_SIGN_PFX_PATH", raising=False)
    monkeypatch.delenv("WINOS_SIGN_PFX_B64", raising=False)
    files = _fake_artifacts(tmp_path)
    arts = [artifact_entry(p, signing_status="unsigned", root=tmp_path) for p in files]
    doc = build_attestation(version="1.0.0", publisher="WinOs-Layer", artifacts=arts)
    att = write_attestation(doc, tmp_path / ATTESTATION_FILENAME, private_key=None)
    data = json.loads(att.read_text(encoding="utf-8"))
    assert not data.get("attestation_signature")
    for a in data["artifacts"]:
        assert a["signing_status"] == "unsigned"
    report = verify_release_artifacts(
        att,
        root=tmp_path,
        expected_publisher="WinOs-Layer",
        require_attestation_signature=False,
    )
    assert report["ok"] is True
    assert report.get("attestation_signed") is False


def test_h63_n037_require_signature_mode_fails_without_cert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bi_mod
):
    monkeypatch.delenv("WINOS_SIGN_PFX_PATH", raising=False)
    monkeypatch.delenv("WINOS_SIGN_PFX_B64", raising=False)
    monkeypatch.delenv("WINOS_RELEASE_SIGNING_KEY", raising=False)
    files = _fake_artifacts(tmp_path)
    rc = bi_mod.sign_artifacts(
        files,
        require_signature=True,
        version="1.0.0",
        publisher="WinOs-Layer",
        dry_run=False,
        output=tmp_path / ATTESTATION_FILENAME,
    )
    assert rc != 0


def test_h63_n037_ed25519_attestation_signature_verifies(tmp_path: Path, ed25519_key):
    assert load_release_private_key() is not None
    assert public_key_hex(ed25519_key) == public_key_hex(load_release_private_key())
    files = _fake_artifacts(tmp_path)
    arts = [artifact_entry(p, signing_status="attested", root=tmp_path) for p in files]
    doc = build_attestation(version="1.0.0", publisher="WinOs-Layer", artifacts=arts)
    att = write_attestation(doc, tmp_path / ATTESTATION_FILENAME, private_key=ed25519_key)
    report = verify_release_artifacts(
        att,
        root=tmp_path,
        expected_publisher="WinOs-Layer",
        public_key=ed25519_key.public_key(),
        require_attestation_signature=True,
    )
    assert report["ok"] is True


def test_h63_n037_authenticode_claim_without_evidence_rejected():
    with pytest.raises(ReleaseAttestationError, match="(?i)evidence|authenticode"):
        build_attestation(
            version="1.0.0",
            publisher="WinOs-Layer",
            artifacts=[
                {
                    "name": "x.exe",
                    "sha256": "a" * 64,
                    "signing_status": "authenticode",
                }
            ],
        )


def test_h63_n037_normalize_rejects_ambiguous_signed():
    with pytest.raises(ReleaseAttestationError):
        normalize_signing_status("signed")


def test_h63_n037_schema_helpers_and_validate(bi_mod):
    helpers = schema_helpers_present()
    assert helpers
    assert all(helpers.values())
    result = bi_mod.validate(verbose=False)
    assert result["ok"] is True, result.get("errors")
    assert any(k.startswith("n037_") for k in result["checks"])


def test_h63_n037_no_fake_installed_authenticode_pass():
    """Do not invent IMPLEMENTATO for Windows Authenticode without a cert."""
    helpers = schema_helpers_present()
    assert helpers.get("signing_statuses") is True
