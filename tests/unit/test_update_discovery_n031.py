"""H63-N031 — Release discovery e download verificato."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from windows_os_api.update.discovery import (
    ReleaseDiscoveryError,
    ReleaseInfo,
    discover_release,
    download_verified,
    validate_https_url,
    version_cmp,
)
from windows_os_api.update.manager import UpdateManager

def _hmac_sig(sha256_hex: str) -> str:
    from windows_os_api.apps.trust.signing import _dev_secret
    digest = sha256_hex.strip().lower().encode("ascii")
    return "hmac-dev:" + hmac.new(_dev_secret(), digest, hashlib.sha256).hexdigest()



def test_version_cmp_and_downgrade_gate(tmp_path: Path):
    assert version_cmp("1.2.3", "1.2.4") == -1
    assert version_cmp("2.0.0", "1.9.9") == 1
    info = ReleaseInfo(
        version="1.0.0",
        artifact_url="https://example.com/a.bin",
        sha256="0" * 64,
    )
    with pytest.raises(ReleaseDiscoveryError, match="downgrade"):
        download_verified(
            info,
            tmp_path,
            current_version="2.0.0",
            allow_downgrade=False,
        )


def test_validate_https_blocks_http_and_credentials():
    with pytest.raises(ReleaseDiscoveryError, match="https"):
        validate_https_url("http://example.com/x")
    with pytest.raises(ReleaseDiscoveryError, match="credentials"):
        validate_https_url("https://user:pass@example.com/x")
    with pytest.raises(ReleaseDiscoveryError, match="blocked"):
        validate_https_url("https://127.0.0.1/x")


def test_discover_and_download_verified(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    payload = b"ARTIFACT-N031"
    digest = hashlib.sha256(payload).hexdigest()
    channel = {
        "version": "3.1.0",
        "artifact_url": "https://releases.example.com/winos-3.1.0.bin",
        "sha256": digest,
        "signature": _hmac_sig(digest),
        "channel": "stable",
    }

    def fake_fetch(url: str, *, max_bytes: int, timeout: float = 30.0) -> bytes:
        if "channel" in url or url.endswith(".json"):
            return json.dumps(channel).encode()
        if url.endswith(".bin"):
            return payload
        raise ReleaseDiscoveryError(f"unexpected url {url}")

    monkeypatch.setattr(
        "windows_os_api.update.discovery._fetch_bytes", fake_fetch
    )
    # Bypass host block for example.com in validate during discover — fake_fetch
    # still calls validate_https_url inside _fetch_bytes. Patch validate for hosts.
    monkeypatch.setattr(
        "windows_os_api.update.discovery._host_blocked", lambda host: False
    )

    info = discover_release("https://releases.example.com/channel.json")
    assert info.version == "3.1.0"
    assert info.sha256 == digest

    dest = download_verified(
        info, tmp_path / "stage", current_version="3.0.0", allow_downgrade=False
    )
    assert dest.read_bytes() == payload
    assert (tmp_path / "stage" / "checksums.txt").is_file()

    # Alter hash → fail
    bad = ReleaseInfo(
        version="3.2.0",
        artifact_url=info.artifact_url,
        sha256="a" * 64,
    )
    with pytest.raises(ReleaseDiscoveryError, match="checksum mismatch"):
        download_verified(bad, tmp_path / "stage2", current_version="3.0.0")


def test_update_manager_discover_and_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    install = tmp_path / "install"
    backup = tmp_path / "backup"
    install.mkdir()
    mgr = UpdateManager(install, backup)
    # force state version
    mgr._state["version"] = "1.0.0"
    mgr._save_state()

    payload = b"NEW"
    digest = hashlib.sha256(payload).hexdigest()
    channel = {
        "version": "1.1.0",
        "artifact_url": "https://releases.example.com/a.bin",
        "sha256": digest,
        "signature": _hmac_sig(digest),
    }

    def fake_fetch(url: str, *, max_bytes: int, timeout: float = 30.0) -> bytes:
        if url.endswith(".json"):
            return json.dumps(channel).encode()
        return payload

    monkeypatch.setattr("windows_os_api.update.discovery._fetch_bytes", fake_fetch)
    monkeypatch.setattr(
        "windows_os_api.update.discovery._host_blocked", lambda host: False
    )

    out = mgr.discover_and_stage(
        "https://releases.example.com/channel.json", tmp_path / "stage"
    )
    assert out["ok"] is True
    assert out["version"] == "1.1.0"
    assert Path(out["path"]).read_bytes() == payload

    # same version refused
    channel["version"] = "1.0.0"
    out2 = mgr.discover_and_stage(
        "https://releases.example.com/channel.json", tmp_path / "stage2"
    )
    assert out2["ok"] is False
    assert "same version" in out2["error"] or "refused" in out2["error"]


def test_invalid_signature_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    payload = b"ARTIFACT-N031-BADSIG"
    digest = hashlib.sha256(payload).hexdigest()

    def fake_fetch(url: str, *, max_bytes: int, timeout: float = 30.0) -> bytes:
        return payload

    monkeypatch.setattr("windows_os_api.update.discovery._fetch_bytes", fake_fetch)
    monkeypatch.setattr(
        "windows_os_api.update.discovery._host_blocked", lambda host: False
    )
    bad = ReleaseInfo(
        version="9.0.0",
        artifact_url="https://releases.example.com/x.bin",
        sha256=digest,
        signature="invalid-signature",
    )
    with pytest.raises(ReleaseDiscoveryError, match="signature"):
        download_verified(bad, tmp_path / "stage", current_version="1.0.0")


def test_missing_signature_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    payload = b"ARTIFACT-N031-NOSIG"
    digest = hashlib.sha256(payload).hexdigest()

    def fake_fetch(url: str, *, max_bytes: int, timeout: float = 30.0) -> bytes:
        return payload

    monkeypatch.setattr("windows_os_api.update.discovery._fetch_bytes", fake_fetch)
    monkeypatch.setattr(
        "windows_os_api.update.discovery._host_blocked", lambda host: False
    )
    info = ReleaseInfo(
        version="9.0.1",
        artifact_url="https://releases.example.com/y.bin",
        sha256=digest,
        signature=None,
    )
    with pytest.raises(ReleaseDiscoveryError, match="signature required"):
        download_verified(info, tmp_path / "stage", current_version="1.0.0")
