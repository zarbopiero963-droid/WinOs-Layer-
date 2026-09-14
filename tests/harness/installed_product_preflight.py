"""N003 — session preflight for installed-product harness (H63-N003 / #67).

Fail-closed gates only:
- occupied port blocks
- FakeBackend / backend==\"fake\" blocks
- missing or mismatched artifact sha256 blocks
- session API key must be really generated (not static suite keys)

Does NOT download/install Windows or Linux artifacts, does NOT drive desktop
hardware, and must never be counted as MANUAL_ONLY PASS or #21 W/L PASS.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Static keys used by the shared FakeBackend suite — never valid for H63-N003.
_FORBIDDEN_STATIC_KEYS = frozenset(
    {
        "dev-key-change-me",
        "admin-key-change-me",
        "smoke-key-not-a-secret",
    }
)


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
    """Parse `sha256  filename` / `sha256 *filename` lines (build_installer style)."""
    mapping: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line)
        if not m:
            continue
        mapping[Path(m.group(2).strip()).name] = m.group(1).lower()
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
        # Astronomically unlikely; regenerate once for determinism of the contract.
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


@dataclass
class SessionPreflightResult:
    """Outcome of a successful fail-closed preflight (ready for external clients)."""

    api_key: str
    identity: ArtifactIdentity
    host: str
    port: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "api_key": self.api_key,
            "identity": self.identity.as_dict(),
            "host": self.host,
            "port": self.port,
            "clients_allowed": ("tcp", "mcp", "ws", "browser"),
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
