"""N003 — installed-product preflight: verified download/install + external clients.

PASS/BLOCK/MALFORMED/EDGE against original H63-N003 contract.
Does NOT claim #21 native systemd/Inno PASS or MANUAL_ONLY desktop PASS.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

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
    serve_bytes_http,
    sha256_file,
    stage_local_artifact,
    teardown_install,
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
    assert payload["clients_implemented"] == declared
    assert payload["clients_allowed"] == declared


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


def test_h63_n003_verified_network_download_pass(tmp_path: Path):
    body = b"verified-network-n003-payload"
    digest = hashlib.sha256(body).hexdigest()
    name = "winos-api"
    checksums = tmp_path / "checksums.txt"
    checksums.write_text(f"{digest}  {name}\n", encoding="utf-8")
    url, stop = serve_bytes_http(body, path=f"/{name}")
    try:
        dest = download_artifact(
            source=url,
            dest_dir=tmp_path / "dl",
            checksums_file=checksums,
            artifact_name=name,
        )
        assert dest.is_file()
        assert sha256_file(dest) == digest
        assert dest.read_bytes() == body
    finally:
        stop()


def test_h63_n003_network_download_bad_checksum_blocks(tmp_path: Path):
    body = b"bad-checksum-body"
    checksums = tmp_path / "checksums.txt"
    checksums.write_text(("0" * 64) + "  winos-api\n", encoding="utf-8")
    url, stop = serve_bytes_http(body, path="/winos-api")
    try:
        with pytest.raises(PreflightError, match="hash mismatch"):
            download_artifact(
                source=url,
                dest_dir=tmp_path / "dl",
                checksums_file=checksums,
                artifact_name="winos-api",
            )
        # Fail-closed: no leftover mismatched file
        assert not (tmp_path / "dl" / "winos-api").exists()
    finally:
        stop()


def test_h63_n003_network_download_redirect_blocks(tmp_path: Path):
    checksums = tmp_path / "checksums.txt"
    checksums.write_text(("a" * 64) + "  winos-api\n", encoding="utf-8")
    url, stop = serve_bytes_http(
        b"x", path="/winos-api", redirect_to="http://127.0.0.1/elsewhere"
    )
    try:
        with pytest.raises(PreflightError, match="redirect denied"):
            download_artifact(
                source=url,
                dest_dir=tmp_path / "dl",
                checksums_file=checksums,
                artifact_name="winos-api",
            )
    finally:
        stop()


def test_h63_n003_ftp_download_fail_closed(tmp_path: Path):
    checksums = tmp_path / "checksums.txt"
    checksums.write_text(("0" * 64) + "  winos-api\n", encoding="utf-8")
    with pytest.raises(PreflightError, match="ftp"):
        download_artifact(
            source="ftp://example.invalid/winos-api",
            dest_dir=tmp_path / "dl",
            checksums_file=checksums,
        )


@pytest.mark.parametrize("target_os", ["linux", "windows"])
def test_h63_n003_install_and_teardown(tmp_path: Path, target_os: str):
    artifact, checksums, digest = _write_artifact(tmp_path, "payload.bin", b"install-n003")
    root = tmp_path / f"install-{target_os}"
    record = install_artifact(
        artifact=artifact,
        target_os=target_os,
        checksums_file=checksums,
        install_root=root,
        version="0.0.0-n003",
    )
    assert record.artifact_sha256 == digest
    assert Path(record.binary_path).is_file()
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "0.0.0-n003"
    assert (root / "WINOS_HARNESS_INSTALL.json").is_file()
    teardown_install(record)
    assert not root.exists()


def test_h63_n003_install_rejects_bad_os_and_missing(tmp_path: Path):
    artifact, checksums, _ = _write_artifact(tmp_path, "winos-api", b"install-n003")
    with pytest.raises(PreflightError, match="target_os"):
        install_artifact(
            artifact=artifact,
            target_os="darwin",
            checksums_file=checksums,
            install_root=tmp_path / "x",
        )
    with pytest.raises(PreflightError, match="install artifact missing"):
        install_artifact(
            artifact=tmp_path / "missing.bin",
            target_os="linux",
            install_root=tmp_path / "y",
        )
    with pytest.raises(PreflightError, match="install_root required"):
        install_artifact(artifact=artifact, target_os="linux", checksums_file=checksums)


def test_h63_n003_install_bad_checksum_blocks(tmp_path: Path):
    artifact = tmp_path / "winos-api"
    artifact.write_bytes(b"body")
    checksums = tmp_path / "checksums.txt"
    checksums.write_text(("0" * 64) + "  winos-api\n", encoding="utf-8")
    with pytest.raises(PreflightError, match="hash mismatch"):
        install_artifact(
            artifact=artifact,
            target_os="linux",
            checksums_file=checksums,
            install_root=tmp_path / "root",
        )


def test_h63_n003_unknown_external_client_rejected():
    with pytest.raises(PreflightError, match="unknown external client"):
        require_external_client("grpc")


def test_h63_n003_mcp_external_client_stdio():
    """MCP external client is a real subprocess (not in-process handle_request)."""
    key = generate_session_api_key()
    # MCP auth uses registry keys; inject a generated key into env for the child
    # via the parent process auth is separate — child uses WINOS_API_KEYS.
    os.environ["WINOS_ADMIN_API_KEYS"] = json.dumps([key])
    os.environ["WINOS_API_KEYS"] = "[]"
    os.environ["WINOS_REQUIRE_AUTH"] = "true"
    from windows_os_api.core.runtime.config import get_settings
    from windows_os_api.core.security.auth import reset_auth_registry

    get_settings.cache_clear()
    reset_auth_registry()
    try:
        with open_external_client("mcp", api_key=key) as client:
            result = client.ping()
            assert result["ok"] is True
            assert result["tool_count"] >= 1
            assert result["server"]["name"] == "winos-mcp"
    finally:
        get_settings.cache_clear()
        reset_auth_registry()


class _UvicornThread:
    def __init__(self, app, host: str, port: int):
        self.host = host
        self.port = port
        self._server = uvicorn.Server(
            uvicorn.Config(app, host=host, port=port, log_level="error", lifespan="on")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._started = threading.Event()
        self._failed = threading.Event()

    def start(self) -> None:
        def _run():
            try:
                self._server.run()
            except BaseException:
                self._failed.set()
                raise
            finally:
                self._failed.set()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if port_is_open_safe(self.port, self.host):
                self._started.set()
                return
            if self._failed.is_set() and not self._thread.is_alive():
                break
            time.sleep(0.05)
        raise RuntimeError("uvicorn failed to start")

    def stop(self) -> None:
        self._server.should_exit = True
        self._server.force_exit = True
        self._thread.join(timeout=15)


def port_is_open_safe(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


@pytest.fixture()
def live_product_server(tmp_path, monkeypatch):
    """Real uvicorn on loopback — required for tcp/ws/browser external clients."""
    key = generate_session_api_key()
    store = tmp_path / "adapter-store"
    store.mkdir()
    # Single role list only — multi-list hits are rejected as ambiguous (N011).
    monkeypatch.setenv("WINOS_ADMIN_API_KEYS", json.dumps([key]))
    monkeypatch.setenv("WINOS_API_KEYS", "[]")
    monkeypatch.setenv("WINOS_REQUIRE_AUTH", "true")
    monkeypatch.setenv("WINOS_BACKEND", "fake")
    monkeypatch.setenv("WINOS_REMOTE_ACCESS_ENABLED", "false")
    monkeypatch.setenv("WINOS_ALLOW_FAKE_FALLBACK", "true")
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(store))

    from windows_os_api.core.events.bus import reset_event_bus
    from windows_os_api.core.runtime import instance_lock as il
    from windows_os_api.core.runtime.app import create_app
    from windows_os_api.core.runtime.config import get_settings
    from windows_os_api.core.security.auth import reset_auth_registry

    get_settings.cache_clear()
    reset_auth_registry()
    reset_event_bus()
    # Drop any leftover lock from a prior failed server in this process.
    try:
        il.release()
    except (RuntimeError, OSError, AttributeError):
        pass

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    app = create_app(get_settings())
    srv = _UvicornThread(app, "127.0.0.1", port)
    srv.start()
    try:
        yield {"host": "127.0.0.1", "port": port, "api_key": key}
    finally:
        srv.stop()
        try:
            il.release()
        except (RuntimeError, OSError, AttributeError):
            pass
        reset_auth_registry()
        reset_event_bus()
        get_settings.cache_clear()


def test_h63_n003_tcp_external_client(live_product_server):
    info = live_product_server
    with open_external_client(
        "tcp", host=info["host"], port=info["port"], api_key=info["api_key"]
    ) as client:
        result = client.ping()
        assert result["ok"] is True
        assert result["status"] == 200
        assert "status" in result["body"] or "live" in result["body"] or "ready" in result["body"]


def test_h63_n003_ws_external_client(live_product_server):
    info = live_product_server
    with open_external_client(
        "ws", host=info["host"], port=info["port"], api_key=info["api_key"]
    ) as client:
        result = client.ping()
        assert result["ok"] is True
        assert result["message"]["type"] == "auth_ok"


def test_h63_n003_browser_external_client(live_product_server):
    info = live_product_server
    with open_external_client(
        "browser", host=info["host"], port=info["port"], api_key=info["api_key"]
    ) as client:
        result = client.ping()
        assert result["ok"] is True
        assert result["control_center_status"] == 200
        assert result["control_center_bytes"] > 100


def test_h63_n003_tcp_client_blocks_when_nothing_listens():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with (
        open_external_client("tcp", host="127.0.0.1", port=port, api_key="x") as client,
        pytest.raises(PreflightError, match="nothing listening"),
    ):
        client.ping()


def test_h63_n003_clients_wired_into_session_preflight(tmp_path: Path, live_product_server):
    info = live_product_server
    artifact, checksums, _ = _write_artifact(tmp_path, "winos-api", b"wire-n003")
    # Port must be free for preflight — use a different free port for the gate,
    # then open clients against the live server.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()
    session = run_session_preflight(
        port=free_port,
        backend="linux",
        artifact=artifact,
        checksums_file=checksums,
        version="0.0.0-n003",
        api_key=info["api_key"],
    )
    assert set(session.as_dict()["clients_implemented"]) == set(EXTERNAL_CLIENT_KINDS)
    # Wire session host/port overridden to live server for tcp ping.
    with open_external_client(
        "tcp",
        session=session,
        host=info["host"],
        port=info["port"],
    ) as client:
        assert client.ping()["ok"] is True
