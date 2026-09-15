"""Release channel discovery + verified download (N031).

HTTPS-only channel JSON → version / artifact URL / sha256 (+ optional signature).
Downloads land in a staging dir, are size-capped, hash-verified (N028), and
refuse downgrades unless ``allow_downgrade=True``.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from windows_os_api.update.checksum_manifest import (
    ChecksumManifestError,
    sha256_file,
    write_checksum_manifest,
)

_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)

DEFAULT_MAX_BYTES = 256 * 1024 * 1024  # 256 MiB


class ReleaseDiscoveryError(ValueError):
    """Fail-closed release discovery / download error."""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    artifact_url: str
    sha256: str
    signature: str | None = None
    channel: str = "stable"
    raw: dict[str, Any] | None = None


def parse_semver(version: str) -> tuple[int, int, int]:
    m = _SEMVER_RE.match((version or "").strip())
    if not m:
        raise ReleaseDiscoveryError(f"invalid semver: {version!r}")
    return int(m.group("major")), int(m.group("minor")), int(m.group("patch"))


def version_cmp(a: str, b: str) -> int:
    """Compare semver core (major.minor.patch); return -1/0/1."""
    aa, bb = parse_semver(a), parse_semver(b)
    if aa < bb:
        return -1
    if aa > bb:
        return 1
    return 0


def _host_blocked(hostname: str) -> bool:
    host = (hostname or "").strip().lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ReleaseDiscoveryError(f"DNS resolve failed for {host!r}: {exc}") from exc
    for info in infos:
        ip_s = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_s)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
    return False


def validate_https_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ReleaseDiscoveryError("only https URLs allowed")
    if parsed.username or parsed.password:
        raise ReleaseDiscoveryError("URL credentials not allowed")
    if not parsed.hostname:
        raise ReleaseDiscoveryError("URL missing host")
    # Block literal IPs that are private etc.
    host = parsed.hostname
    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ReleaseDiscoveryError(f"blocked host IP: {host}")
    except ValueError:
        if _host_blocked(host):
            raise ReleaseDiscoveryError(f"blocked host: {host}") from None
    return url


def _fetch_bytes(url: str, *, max_bytes: int, timeout: float = 30.0) -> bytes:
    validate_https_url(url)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "WinOs-Layer-updater/N031", "Accept": "application/json,application/octet-stream"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — validated https
            # Refuse redirects to non-https by not enabling custom redirect handler;
            # urllib follows redirects — re-validate final URL.
            final = resp.geturl()
            validate_https_url(final)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ReleaseDiscoveryError(
                        f"response exceeds max_bytes={max_bytes}"
                    )
                chunks.append(chunk)
            return b"".join(chunks)
    except urllib.error.HTTPError as exc:
        raise ReleaseDiscoveryError(f"HTTP {exc.code} fetching {url}") from exc
    except urllib.error.URLError as exc:
        raise ReleaseDiscoveryError(f"fetch failed: {exc}") from exc


def discover_release(channel_url: str, *, max_bytes: int = 1_000_000) -> ReleaseInfo:
    """Fetch and parse a channel manifest JSON over HTTPS."""
    raw = _fetch_bytes(channel_url, max_bytes=max_bytes)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseDiscoveryError(f"channel JSON invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise ReleaseDiscoveryError("channel JSON must be an object")
    version = str(data.get("version") or "").strip()
    artifact_url = str(data.get("artifact_url") or data.get("url") or "").strip()
    sha256 = str(data.get("sha256") or data.get("checksum_sha256") or "").strip().lower()
    if not version or not artifact_url or not sha256:
        raise ReleaseDiscoveryError("channel missing version/artifact_url/sha256")
    parse_semver(version)
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ReleaseDiscoveryError("invalid sha256 in channel")
    validate_https_url(artifact_url)
    sig = data.get("signature")
    signature = str(sig).strip() if sig else None
    channel = str(data.get("channel") or "stable")
    return ReleaseInfo(
        version=version,
        artifact_url=artifact_url,
        sha256=sha256,
        signature=signature,
        channel=channel,
        raw=data,
    )


def download_verified(
    release: ReleaseInfo,
    staging_dir: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    current_version: str | None = None,
    allow_downgrade: bool = False,
) -> Path:
    """Download artifact to staging, verify sha256, write sidecar checksums.txt."""
    if current_version and not allow_downgrade:
        if version_cmp(release.version, current_version) < 0:
            raise ReleaseDiscoveryError(
                f"downgrade refused: {release.version} < {current_version}"
            )
        if version_cmp(release.version, current_version) == 0:
            raise ReleaseDiscoveryError(
                f"same version refused: {release.version}"
            )

    staging_dir = Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    name = Path(urlparse(release.artifact_url).path).name or f"release-{release.version}.bin"
    # Prevent path tricks
    name = Path(name).name
    dest = staging_dir / name
    payload = _fetch_bytes(release.artifact_url, max_bytes=max_bytes)
    dest.write_bytes(payload)
    actual = sha256_file(dest)
    if actual != release.sha256:
        try:
            dest.unlink()
        except OSError:
            pass
        raise ReleaseDiscoveryError(
            f"checksum mismatch: expected {release.sha256}, got {actual}"
        )
    # Sidecar manifest for the staged artifact (N028)
    try:
        write_checksum_manifest([dest], staging_dir / "checksums.txt", root=staging_dir)
    except ChecksumManifestError as exc:
        raise ReleaseDiscoveryError(str(exc)) from exc
    return dest
