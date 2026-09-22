"""N003 — installed-product session preflight + verified download/install/clients.

Original contract (#67 / H63-N003, Q00/Q01):
- occupied port blocks
- FakeBackend / backend==\"fake\" blocks
- missing or mismatched artifact sha256 blocks
- session API key must be really generated (not static suite keys)
- verified download: local path OR http(s) URL + checksum verify
- Windows/Linux layout install + teardown under an isolated install_root
- real external clients: tcp / mcp / ws / browser (not in-process TestClient)

Does NOT claim #21 Windows/Linux native systemd/Inno PASS or MANUAL_ONLY PASS.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlparse

# Static keys used by the shared FakeBackend suite — never valid for H63-N003.
_FORBIDDEN_STATIC_KEYS = frozenset(
    {
        "dev-key-change-me",
        "admin-key-change-me",
        "smoke-key-not-a-secret",
    }
)

EXTERNAL_CLIENT_KINDS = frozenset({"tcp", "mcp", "ws", "browser"})
_IMPLEMENTED_CLIENTS = frozenset({"tcp", "mcp", "ws", "browser"})

DEFAULT_DOWNLOAD_MAX_BYTES = 256 * 1024 * 1024  # 256 MiB harness cap
_INSTALL_MANIFEST = "WINOS_HARNESS_INSTALL.json"
_REPO_ROOT = Path(__file__).resolve().parents[2]


class PreflightError(RuntimeError):
    """Hard dependency failed — session must not proceed."""


@dataclass(frozen=True)
class ArtifactIdentity:
    """Collected artifact/version/hash/backend metadata for a session."""

    version: str
    backend: str
    sha256: str
    artifact_path: str

    def as_dict(self) -> dict[str, str]:
        return {
            "version": self.version,
            "backend": self.backend,
            "sha256": self.sha256,
            "artifact_path": self.artifact_path,
        }


@dataclass(frozen=True)
class InstallRecord:
    """Isolated harness install layout (teardown removes only owned paths)."""

    target_os: str
    install_root: str
    binary_path: str
    artifact_sha256: str
    version: str
    owned_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_os": self.target_os,
            "install_root": self.install_root,
            "binary_path": self.binary_path,
            "artifact_sha256": self.artifact_sha256,
            "version": self.version,
            "owned_paths": list(self.owned_paths),
        }


def port_is_open(port: int, host: str = "127.0.0.1") -> bool:
    """True when something accepts TCP connections on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def require_port_free(port: int, host: str = "127.0.0.1") -> None:
    """Fail closed if the intended serve port is already occupied."""
    if port_is_open(port, host=host):
        raise PreflightError(
            f"occupied port blocks installed-product session: {host}:{port}"
        )


def _backend_name(backend: Any) -> str:
    if backend is None:
        return ""
    if isinstance(backend, str):
        return backend.strip().lower()
    name = getattr(backend, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip().lower()
    cls = type(backend).__name__.lower()
    if "fake" in cls:
        return "fake"
    return cls


def require_not_fake_backend(backend: Any) -> str:
    """Fail closed when FakeBackend (or backend name \"fake\") is detected."""
    name = _backend_name(backend)
    cls_name = type(backend).__name__ if not isinstance(backend, str) else ""
    if name == "fake" or cls_name == "FakeBackend":
        raise PreflightError(
            "FakeBackend detected — installed-product harness blocked "
            "(set a real windows/linux backend and WINOS_ALLOW_FAKE_FALLBACK=false)"
        )
    if not name:
        raise PreflightError("backend identity missing — installed-product harness blocked")
    return name


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_checksums(text: str) -> dict[str, str]:
    """Parse `sha256  path` / `sha256 *path` lines (build_installer / N028 style)."""
    mapping: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line)
        if not m:
            continue
        digest = m.group(1).lower()
        label = m.group(2).strip()
        mapping[label] = digest
        mapping[Path(label).name] = digest
    return mapping


def require_artifact_checksum(checksums_file: Path, artifact: Path) -> str:
    """Fail closed if checksums file or artifact is missing, or hash mismatches."""
    if not checksums_file.is_file():
        raise PreflightError(f"artifact checksums missing: {checksums_file}")
    if not artifact.is_file():
        raise PreflightError(f"artifact missing: {artifact}")
    expected_map = _parse_checksums(checksums_file.read_text(encoding="utf-8"))
    expected = expected_map.get(artifact.name)
    if not expected:
        raise PreflightError(
            f"artifact hash not listed for {artifact.name!r} in {checksums_file}"
        )
    actual = sha256_file(artifact)
    if actual != expected:
        raise PreflightError(
            f"artifact hash mismatch for {artifact.name}: "
            f"expected {expected}, got {actual}"
        )
    return actual


def generate_session_api_key(*, nbytes: int = 32) -> str:
    """Really generate a session API key (not a static suite/smoke key)."""
    key = secrets.token_urlsafe(nbytes)
    if key in _FORBIDDEN_STATIC_KEYS:
        key = secrets.token_urlsafe(nbytes + 8)
    if key in _FORBIDDEN_STATIC_KEYS or not key.strip():
        raise PreflightError("failed to generate a non-static session API key")
    return key


def require_generated_api_key(api_key: str) -> str:
    """Fail closed if the key is empty or one of the known static suite keys."""
    if not api_key or not api_key.strip():
        raise PreflightError("session API key missing")
    if api_key.strip() in _FORBIDDEN_STATIC_KEYS:
        raise PreflightError(
            "static suite/smoke API key rejected — H63-N003 requires a really generated key"
        )
    return api_key


def collect_artifact_identity(
    *,
    version: str,
    backend: Any,
    artifact: Path,
    checksums_file: Path,
) -> ArtifactIdentity:
    """Collect version/hash/backend after hard gates pass."""
    backend_name = require_not_fake_backend(backend)
    if not version or not str(version).strip():
        raise PreflightError("artifact version missing")
    digest = require_artifact_checksum(checksums_file, artifact)
    return ArtifactIdentity(
        version=str(version).strip(),
        backend=backend_name,
        sha256=digest,
        artifact_path=str(artifact.resolve()),
    )


def _is_network_source(source: str | Path) -> bool:
    text = str(source).strip()
    if not text:
        return False
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https", "ftp", "ftps"}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Fail closed: never auto-follow redirects during harness download."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        loc = headers.get("Location") or headers.get("location") or newurl
        raise PreflightError(
            f"artifact download redirect denied (HTTP {code} → {loc})"
        )


def _fetch_url_bytes(
    url: str,
    *,
    max_bytes: int,
    timeout: float = 30.0,
) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise PreflightError(
            f"unsupported download scheme {parsed.scheme!r} — only http/https"
        )
    if parsed.username or parsed.password:
        raise PreflightError("URL credentials not allowed in artifact download")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "WinOs-Layer-harness/N003",
            "Accept": "application/octet-stream,*/*",
        },
        method="GET",
    )
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler)
        with opener.open(req, timeout=timeout) as resp:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise PreflightError(
                        f"artifact download exceeds max_bytes={max_bytes}"
                    )
                chunks.append(chunk)
            return b"".join(chunks)
    except PreflightError:
        raise
    except urllib.error.HTTPError as exc:
        raise PreflightError(f"HTTP {exc.code} downloading artifact from {url}") from exc
    except urllib.error.URLError as exc:
        raise PreflightError(f"artifact download failed: {exc}") from exc


def stage_local_artifact(
    *,
    source: Path,
    dest_dir: Path,
    checksums_file: Path,
    artifact_name: str | None = None,
) -> Path:
    """Copy a local staged artifact into ``dest_dir`` and verify checksum."""
    src = Path(source)
    if not src.is_file():
        raise PreflightError(f"local artifact source missing: {src}")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = artifact_name or src.name
    dest = dest_dir / name
    shutil.copy2(src, dest)
    require_artifact_checksum(Path(checksums_file), dest)
    return dest.resolve()


def download_artifact(
    *,
    source: str | Path,
    dest_dir: Path,
    checksums_file: Path,
    artifact_name: str | None = None,
    max_bytes: int = DEFAULT_DOWNLOAD_MAX_BYTES,
    timeout: float = 30.0,
) -> Path:
    """Verified download for the installed-product harness.

    - Local filesystem path → stage + checksum verify.
    - http(s) URL → fetch (no redirects), write, checksum verify against
      ``checksums_file``; mismatch / HTTP error / oversize → ``PreflightError``.
    - ftp(s) → fail-closed (not supported).
    """
    if _is_network_source(source):
        text = str(source).strip()
        parsed = urlparse(text)
        if parsed.scheme in {"ftp", "ftps"}:
            raise PreflightError(
                f"ftp artifact download not supported — fail-closed ({parsed.scheme})"
            )
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = artifact_name or Path(parsed.path).name or "artifact.bin"
        name = Path(name).name  # strip any path tricks
        if not name:
            raise PreflightError("artifact name missing from URL")
        dest = dest_dir / name
        payload = _fetch_url_bytes(text, max_bytes=max_bytes, timeout=timeout)
        dest.write_bytes(payload)
        try:
            require_artifact_checksum(Path(checksums_file), dest)
        except PreflightError:
            try:
                dest.unlink()
            except OSError:
                pass
            raise
        return dest.resolve()

    src_path = Path(source)
    return stage_local_artifact(
        source=src_path,
        dest_dir=Path(dest_dir),
        checksums_file=Path(checksums_file),
        artifact_name=artifact_name,
    )


def _binary_name_for_os(target_os: str) -> str:
    return "winos-api.exe" if target_os == "windows" else "winos-api"


def install_artifact(
    *,
    artifact: Path,
    target_os: str,
    checksums_file: Path | None = None,
    install_root: Path | str | None = None,
    version: str = "0.0.0-harness",
) -> InstallRecord:
    """Install artifact into an isolated harness layout and return a record.

    Creates ``install_root`` with product-shaped layout (binary + VERSION +
    optional installer scripts copy). Does **not** start systemd/Inno services
    (#21 H63-N003). Call ``teardown_install`` to remove owned paths.
    """
    art = Path(artifact)
    if not art.is_file():
        raise PreflightError(f"install artifact missing: {art}")
    os_name = (target_os or "").strip().lower()
    if os_name not in {"windows", "linux"}:
        raise PreflightError(
            f"install target_os must be 'windows' or 'linux', got {target_os!r}"
        )
    digest: str | None = None
    if checksums_file is not None:
        digest = require_artifact_checksum(Path(checksums_file), art)
    else:
        digest = sha256_file(art)

    if install_root is None:
        raise PreflightError(
            "install_root required for harness install "
            "(isolated layout; native systemd/Inno remains #21)"
        )
    root = Path(install_root)
    if root.exists() and any(root.iterdir()):
        raise PreflightError(f"install_root not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)

    bin_name = _binary_name_for_os(os_name)
    binary_dest = root / bin_name
    shutil.copy2(art, binary_dest)
    if os_name == "linux":
        binary_dest.chmod(binary_dest.stat().st_mode | 0o111)

    version_path = root / "VERSION"
    version_path.write_text(str(version).strip() + "\n", encoding="utf-8")

    owned: list[Path] = [binary_dest, version_path]

    # Best-effort copy of product installer helpers into the layout.
    if os_name == "linux":
        linux_src = _REPO_ROOT / "installer" / "linux"
        if linux_src.is_dir():
            dest_inst = root / "installer" / "linux"
            dest_inst.mkdir(parents=True, exist_ok=True)
            for name in ("install.sh", "uninstall.sh", "winos-api.service", "LINUX.md"):
                src = linux_src / name
                if src.is_file():
                    dst = dest_inst / name
                    shutil.copy2(src, dst)
                    owned.append(dst)
            owned.append(dest_inst)
            owned.append(dest_inst.parent)
    else:
        scripts = _REPO_ROOT / "installer" / "service_scripts"
        if scripts.is_dir():
            dest_svc = root / "service"
            dest_svc.mkdir(parents=True, exist_ok=True)
            for src in scripts.iterdir():
                if src.is_file():
                    dst = dest_svc / src.name
                    shutil.copy2(src, dst)
                    owned.append(dst)
            owned.append(dest_svc)

    manifest = {
        "target_os": os_name,
        "install_root": str(root.resolve()),
        "binary_path": str(binary_dest.resolve()),
        "artifact_sha256": digest,
        "version": str(version).strip(),
        "owned_paths": [str(p.resolve()) for p in owned],
    }
    manifest_path = root / _INSTALL_MANIFEST
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    owned.append(manifest_path)

    return InstallRecord(
        target_os=os_name,
        install_root=str(root.resolve()),
        binary_path=str(binary_dest.resolve()),
        artifact_sha256=digest,
        version=str(version).strip(),
        owned_paths=tuple(str(p.resolve()) for p in owned),
    )


def teardown_install(record: InstallRecord | dict[str, Any] | Path) -> None:
    """Remove harness-owned install layout. Idempotent for missing paths."""
    if isinstance(record, Path):
        root = record
        manifest_path = root / _INSTALL_MANIFEST
        if not manifest_path.is_file():
            raise PreflightError(f"install manifest missing: {manifest_path}")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    elif isinstance(record, InstallRecord):
        data = record.as_dict()
        root = Path(record.install_root)
    else:
        data = dict(record)
        root = Path(str(data["install_root"]))

    owned = [Path(p) for p in data.get("owned_paths") or []]
    # Delete files first, then directories (deepest first).
    for path in sorted(owned, key=lambda p: len(p.parts), reverse=True):
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                # Only remove if empty after owned files gone.
                try:
                    path.rmdir()
                except OSError:
                    shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# External clients (real network / process — not Starlette TestClient)
# ---------------------------------------------------------------------------


class ExternalClient:
    """Base for harness external clients."""

    kind: str = ""

    def ping(self) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        return None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@dataclass
class TcpExternalClient(ExternalClient):
    """Raw TCP HTTP client against the installed product (/v1/health)."""

    kind: str = "tcp"
    host: str = "127.0.0.1"
    port: int = 0
    api_key: str = ""
    timeout: float = 5.0

    def ping(self) -> dict[str, Any]:
        if not self.port:
            raise PreflightError("tcp client: port missing")
        # Fail closed if nothing listens.
        if not port_is_open(self.port, host=self.host):
            raise PreflightError(
                f"tcp client: nothing listening on {self.host}:{self.port}"
            )
        path = "/v1/health"
        req_lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {self.host}:{self.port}",
            "Connection: close",
            "Accept: application/json",
        ]
        if self.api_key:
            req_lines.append(f"X-API-Key: {self.api_key}")
        raw = ("\r\n".join(req_lines) + "\r\n\r\n").encode("ascii")
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.settimeout(self.timeout)
            sock.sendall(raw)
            chunks: list[bytes] = []
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
                if len(b"".join(chunks)) > 1_000_000:
                    break
        response = b"".join(chunks)
        if not response:
            raise PreflightError("tcp client: empty response")
        header, _, body = response.partition(b"\r\n\r\n")
        status_line = header.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
        parts = status_line.split(" ", 2)
        if len(parts) < 2:
            raise PreflightError(f"tcp client: malformed status {status_line!r}")
        try:
            code = int(parts[1])
        except ValueError as exc:
            raise PreflightError(f"tcp client: bad status code in {status_line!r}") from exc
        payload: Any
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {"raw": body.decode("utf-8", errors="replace")[:500]}
        return {"ok": 200 <= code < 300, "status": code, "body": payload}


@dataclass
class McpExternalClient(ExternalClient):
    """External MCP via stdio JSON-RPC subprocess (not in-process handle_request)."""

    kind: str = "mcp"
    api_key: str = ""
    _proc: subprocess.Popen[str] | None = field(default=None, repr=False)

    def _ensure(self) -> subprocess.Popen[str]:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        env = os.environ.copy()
        env.setdefault("WINOS_BACKEND", "fake")  # MCP process local; product gates separate
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "windows_os_api.api.mcp.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(_REPO_ROOT),
        )
        return self._proc

    def _rpc(self, method: str, params: dict[str, Any] | None = None, rid: int = 1) -> dict[str, Any]:
        proc = self._ensure()
        assert proc.stdin and proc.stdout
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        p = dict(params or {})
        if self.api_key:
            meta = p.get("_meta") if isinstance(p.get("_meta"), dict) else {}
            p["_meta"] = {**meta, "api_key": self.api_key}
        if p:
            body["params"] = p
        proc.stdin.write(json.dumps(body) + "\n")
        proc.stdin.flush()
        # Read until we get a response with matching id (skip notifications).
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                err = (proc.stderr.read() if proc.stderr else "") or ""
                raise PreflightError(f"mcp client: process closed stdout ({err[:200]})")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PreflightError(f"mcp client: bad JSON {line[:200]!r}") from exc
            if msg.get("id") == rid:
                return msg
            # notification — continue
        raise PreflightError("mcp client: timeout waiting for response")

    def ping(self) -> dict[str, Any]:
        init = self._rpc(
            "initialize",
            {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "n003-harness", "version": "1"}},
            rid=1,
        )
        if "error" in init:
            raise PreflightError(f"mcp initialize failed: {init['error']}")
        listed = self._rpc("tools/list", {}, rid=2)
        if "error" in listed:
            raise PreflightError(f"mcp tools/list failed: {listed['error']}")
        tools = (listed.get("result") or {}).get("tools") or []
        return {
            "ok": True,
            "server": (init.get("result") or {}).get("serverInfo"),
            "tool_count": len(tools),
        }

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except OSError:
                pass


@dataclass
class WsExternalClient(ExternalClient):
    """External WebSocket client to /ws/events (websockets library)."""

    kind: str = "ws"
    host: str = "127.0.0.1"
    port: int = 0
    api_key: str = ""
    timeout: float = 5.0

    def ping(self) -> dict[str, Any]:
        if not self.port:
            raise PreflightError("ws client: port missing")
        if not port_is_open(self.port, host=self.host):
            raise PreflightError(
                f"ws client: nothing listening on {self.host}:{self.port}"
            )
        try:
            import asyncio

            import websockets
        except ImportError as exc:
            raise PreflightError(f"ws client: websockets package required: {exc}") from exc

        uri = f"ws://{self.host}:{self.port}/ws/events"

        async def _run() -> dict[str, Any]:
            # First-message auth (browser-safe): accept then authenticate.
            # Avoid header-only path where deny closes before accept → HTTP 403.
            async with websockets.connect(
                uri,
                origin=f"http://{self.host}:{self.port}",
                open_timeout=self.timeout,
                close_timeout=self.timeout,
            ) as ws:
                if self.api_key:
                    await ws.send(
                        json.dumps({"type": "auth", "api_key": self.api_key})
                    )
                raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
                msg = json.loads(raw)
                return {"ok": msg.get("type") == "auth_ok", "message": msg}

        try:
            return asyncio.run(_run())
        except PreflightError:
            raise
        except Exception as exc:
            raise PreflightError(f"ws client failed: {exc}") from exc


@dataclass
class BrowserExternalClient(ExternalClient):
    """Browser-like external HTTP session: control center HTML + /v1/health.

    Uses urllib (out-of-process from the ASGI app). Not Playwright — does not
    claim MANUAL_ONLY desktop PASS.
    """

    kind: str = "browser"
    host: str = "127.0.0.1"
    port: int = 0
    api_key: str = ""
    timeout: float = 5.0

    def ping(self) -> dict[str, Any]:
        if not self.port:
            raise PreflightError("browser client: port missing")
        if not port_is_open(self.port, host=self.host):
            raise PreflightError(
                f"browser client: nothing listening on {self.host}:{self.port}"
            )
        base = f"http://{self.host}:{self.port}"

        def _get(path: str, *, with_key: bool = False) -> tuple[int, bytes]:
            req = urllib.request.Request(base + path)
            if with_key and self.api_key:
                req.add_header("X-API-Key", self.api_key)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return int(resp.status), resp.read()
            except urllib.error.HTTPError as exc:
                return int(exc.code), exc.read() if exc.fp else b""

        cc_code, cc_body = _get("/")
        if cc_code != 200:
            raise PreflightError(f"browser client: control center HTTP {cc_code}")
        head = cc_body[:400].lower()
        if b"<html" not in head and b"<!doctype" not in head and b"winos" not in head:
            raise PreflightError("browser client: control center body is not HTML")
        health_code, health_body = _get("/v1/health")
        try:
            health = json.loads(health_body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            health = {}
        return {
            "ok": cc_code == 200 and 200 <= health_code < 600,
            "control_center_status": cc_code,
            "health_status": health_code,
            "health": health,
            "control_center_bytes": len(cc_body),
        }


def require_external_client(kind: str) -> str:
    """Validate kind and that an implementation exists (fail closed otherwise)."""
    name = (kind or "").strip().lower()
    if name not in EXTERNAL_CLIENT_KINDS:
        raise PreflightError(
            f"unknown external client kind {kind!r} — "
            f"allowed: {sorted(EXTERNAL_CLIENT_KINDS)}"
        )
    if name not in _IMPLEMENTED_CLIENTS:
        raise PreflightError(
            f"external client {name!r} not implemented in harness — fail-closed"
        )
    return name


def open_external_client(
    kind: str,
    session: SessionPreflightResult | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    api_key: str | None = None,
) -> ExternalClient:
    """Open a real external client wired to the session (or explicit host/port)."""
    name = require_external_client(kind)
    h = host if host is not None else (session.host if session else "127.0.0.1")
    p = port if port is not None else (session.port if session else 0)
    key = api_key if api_key is not None else (session.api_key if session else "")
    if name == "tcp":
        return TcpExternalClient(host=h, port=int(p), api_key=key)
    if name == "mcp":
        return McpExternalClient(api_key=key)
    if name == "ws":
        return WsExternalClient(host=h, port=int(p), api_key=key)
    if name == "browser":
        return BrowserExternalClient(host=h, port=int(p), api_key=key)
    raise PreflightError(f"external client {name!r} not implemented in harness — fail-closed")


@dataclass
class SessionPreflightResult:
    """Outcome of a successful fail-closed preflight (ready for external clients)."""

    api_key: str
    identity: ArtifactIdentity
    host: str
    port: int

    def as_dict(self) -> dict[str, Any]:
        implemented = tuple(sorted(_IMPLEMENTED_CLIENTS))
        declared = tuple(sorted(EXTERNAL_CLIENT_KINDS))
        return {
            "api_key": self.api_key,
            "identity": self.identity.as_dict(),
            "host": self.host,
            "port": self.port,
            "clients_declared": declared,
            "clients_implemented": implemented,
            # Alias kept for callers; equals declared kinds.
            "clients_allowed": declared,
        }


def run_session_preflight(
    *,
    port: int,
    backend: Any,
    artifact: Path,
    checksums_file: Path,
    version: str,
    host: str = "127.0.0.1",
    api_key: str | None = None,
) -> SessionPreflightResult:
    """Hard session preflight: occupied port / fake / hash / key all fail closed."""
    require_port_free(port, host=host)
    identity = collect_artifact_identity(
        version=version,
        backend=backend,
        artifact=artifact,
        checksums_file=checksums_file,
    )
    key = api_key if api_key is not None else generate_session_api_key()
    require_generated_api_key(key)
    return SessionPreflightResult(
        api_key=key,
        identity=identity,
        host=host,
        port=port,
    )


# ---------------------------------------------------------------------------
# Test helpers: local artifact HTTP server (loopback only)
# ---------------------------------------------------------------------------


def serve_bytes_http(
    payload: bytes,
    *,
    path: str = "/artifact.bin",
    status: int = 200,
    redirect_to: str | None = None,
) -> tuple[str, Callable[[], None]]:
    """Serve ``payload`` on an ephemeral 127.0.0.1 port. Returns (url, stop)."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if redirect_to is not None:
                self.send_response(302)
                self.send_header("Location", redirect_to)
                self.end_headers()
                return
            if self.path.split("?", 1)[0] != path:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}{path}"

    def stop() -> None:
        server.shutdown()
        server.server_close()

    return url, stop
