"""N003 — installed-product session preflight + fail-closed hooks (H63-N003 / #67).

Unit-level fail-closed contract:
- occupied port blocks
- FakeBackend / backend==fake blocks
- missing / mismatched artifact hash blocks
- auth key must be really generated (static suite keys rejected)
- local download staging + checksum; network URL fail-closed
- OS install hook always fail-closed
- external client stubs (tcp/mcp/ws/browser) fail-closed

Does NOT claim installed W/L (#21) PASS or MANUAL_ONLY PASS.
"""
from __future__ import annotations

import hashlib
import socket
from pathlib import Path

import pytest

from tests.harness.installed_product_preflight import (
    EXTERNAL_CLIENT_KINDS,
    PreflightError,
    collect_artifact_identity,
    download_artifact,
    generate_session_api_key,
    install_artifact,
    open_external_client,
    require_artifact_checksum,
    require_external_client,
    require_generated_api_key,
    require_not_fake_backend,
    require_port_free,
    run_session_preflight,
    sha256_file,
    stage_local_artifact,
)


def _write_artifact(tmp: Path, name: str, body: bytes) -> tuple[Path, Path, str]:
    artifact = tmp / name
    artifact.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    checksums = tmp / "checksums.txt"
    checksums.write_text(f"{digest}  {name}\n", encoding="utf-8")
    return artifact, checksums, digest


def test_h63_n003_occupied_port_blocks():
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        with pytest.raises(PreflightError, match="occupied port"):
            require_port_free(port)
    finally:
        holder.close()


def test_h63_n003_free_port_passes():
    # Bind then close so the port is free again (best-effort).
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    require_port_free(port)


def test_h63_n003_fake_backend_string_blocks():
    with pytest.raises(PreflightError, match="FakeBackend"):
        require_not_fake_backend("fake")


def test_h63_n003_fake_backend_instance_blocks():
    from windows_os_api.backends.fake import FakeBackend

    with pytest.raises(PreflightError, match="FakeBackend"):
        require_not_fake_backend(FakeBackend())


@pytest.mark.parametrize("backend", ["windows", "linux"])
def test_h63_n003_real_backend_name_passes(backend: str):
    assert require_not_fake_backend(backend) == backend


def test_h63_n003_missing_checksums_file_blocks(tmp_path: Path):
    artifact = tmp_path / "winos-api"
    artifact.write_bytes(b"n003")
    with pytest.raises(PreflightError, match="checksums missing"):
        require_artifact_checksum(tmp_path / "nope.txt", artifact)


def test_h63_n003_missing_artifact_blocks(tmp_path: Path):
    checksums = tmp_path / "checksums.txt"
    checksums.write_text("a" * 64 + "  winos-api\n", encoding="utf-8")
    with pytest.raises(PreflightError, match="artifact missing"):
        require_artifact_checksum(checksums, tmp_path / "winos-api")


def test_h63_n003_hash_mismatch_blocks(tmp_path: Path):
    artifact = tmp_path / "winos-api"
    artifact.write_bytes(b"n003-body")
    checksums = tmp_path / "checksums.txt"
    checksums.write_text(("0" * 64) + "  winos-api\n", encoding="utf-8")
    with pytest.raises(PreflightError, match="hash mismatch"):
        require_artifact_checksum(checksums, artifact)


def test_h63_n003_hash_match_passes(tmp_path: Path):
    artifact, checksums, digest = _write_artifact(tmp_path, "winos-api", b"ok-n003")
    assert require_artifact_checksum(checksums, artifact) == digest
    assert sha256_file(artifact) == digest


def test_h63_n003_static_api_keys_rejected():
    for bad in ("dev-key-change-me", "admin-key-change-me", "smoke-key-not-a-secret", ""):
        with pytest.raises(PreflightError):
            require_generated_api_key(bad)


def test_h63_n003_generate_session_api_key_is_not_static():
    key = generate_session_api_key()
    require_generated_api_key(key)
    assert key not in {
        "dev-key-change-me",
        "admin-key-change-me",
        "smoke-key-not-a-secret",
    }
    assert len(key) >= 32


def test_h63_n003_collect_identity_and_session_preflight(tmp_path: Path):
    artifact, checksums, digest = _write_artifact(tmp_path, "winos-api", b"session-n003")
    # free port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    identity = collect_artifact_identity(
        version="0.0.0-n003",
        backend="linux",
        artifact=artifact,
        checksums_file=checksums,
    )
    assert identity.sha256 == digest
    assert identity.backend == "linux"

    result = run_session_preflight(
        port=port,
        backend="linux",
        artifact=artifact,
        checksums_file=checksums,
        version="0.0.0-n003",
    )
    assert result.port == port
    assert result.identity.sha256 == digest
    require_generated_api_key(result.api_key)
    payload = result.as_dict()
    declared = tuple(sorted(EXTERNAL_CLIENT_KINDS))
    assert payload["clients_declared"] == declared
    assert payload["clients_implemented"] == ()
    assert payload["clients_allowed"] == declared  # alias; not "ready"


def test_h63_n003_session_preflight_blocks_occupied_or_fake(tmp_path: Path):
    artifact, checksums, _ = _write_artifact(tmp_path, "winos-api", b"block-n003")
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        with pytest.raises(PreflightError, match="occupied port"):
            run_session_preflight(
                port=port,
                backend="linux",
                artifact=artifact,
                checksums_file=checksums,
                version="0.0.0-n003",
            )
    finally:
        holder.close()

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    with pytest.raises(PreflightError, match="FakeBackend"):
        run_session_preflight(
            port=free,
            backend="fake",
            artifact=artifact,
            checksums_file=checksums,
            version="0.0.0-n003",
        )


def test_h63_n003_stage_local_artifact_and_download_hook(tmp_path: Path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    artifact, checksums, digest = _write_artifact(source_dir, "winos-api", b"stage-n003")
    dest_dir = tmp_path / "staged"
    staged = stage_local_artifact(
        source=artifact,
        dest_dir=dest_dir,
        checksums_file=checksums,
    )
    assert staged.is_file()
    assert sha256_file(staged) == digest

    via_download = download_artifact(
        source=artifact,
        dest_dir=tmp_path / "dl",
        checksums_file=checksums,
    )
    assert via_download.is_file()
    assert sha256_file(via_download) == digest


def test_h63_n003_network_download_fail_closed(tmp_path: Path):
    checksums = tmp_path / "checksums.txt"
    checksums.write_text(("0" * 64) + "  winos-api\n", encoding="utf-8")
    with pytest.raises(PreflightError, match="network artifact download not implemented"):
        download_artifact(
            source="https://example.invalid/release/winos-api",
            dest_dir=tmp_path / "dl",
            checksums_file=checksums,
        )


def test_h63_n003_install_artifact_always_fail_closed(tmp_path: Path):
    artifact, checksums, _ = _write_artifact(tmp_path, "winos-api", b"install-n003")
    with pytest.raises(PreflightError, match="OS install not implemented"):
        install_artifact(
            artifact=artifact,
            target_os="linux",
            checksums_file=checksums,
        )
    with pytest.raises(PreflightError, match="OS install not implemented"):
        install_artifact(artifact=artifact, target_os="windows")
    with pytest.raises(PreflightError, match="target_os"):
        install_artifact(artifact=artifact, target_os="darwin")
    with pytest.raises(PreflightError, match="install artifact missing"):
        install_artifact(artifact=tmp_path / "missing.bin", target_os="linux")


@pytest.mark.parametrize("kind", sorted(EXTERNAL_CLIENT_KINDS))
def test_h63_n003_external_client_stubs_fail_closed(kind: str):
    with pytest.raises(PreflightError, match="not implemented"):
        require_external_client(kind)
    with pytest.raises(PreflightError, match="not implemented"):
        open_external_client(kind)


def test_h63_n003_unknown_external_client_rejected():
    with pytest.raises(PreflightError, match="unknown external client"):
        require_external_client("grpc")
