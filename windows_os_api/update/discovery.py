"""Release channel discovery + verified download (N031).

HTTPS-only channel JSON → version / artifact URL / sha256 + required signature.
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


def verify_release_signature(*, sha256_hex: str, signature: str | None) -> None:
    """N031: require and verify release signature over the artifact sha256.

    Accepted forms (same prefixes as adapter trust):
    * ``ed25519:<key_id>:<b64url>`` — Ed25519 over ASCII sha256 hex, active keystore key
    * ``hmac-dev:<hex>`` — HMAC-SHA256 over ASCII sha256 hex (dev only)

    Missing or invalid → ``ReleaseDiscoveryError`` (fail-closed).
    """
    sig = (signature or "").strip()
    if not sig:
        raise ReleaseDiscoveryError("release signature required")
    digest = (sha256_hex or "").strip().lower().encode("ascii")
    if len(digest) != 64:
        raise ReleaseDiscoveryError("cannot verify signature: invalid sha256")

    # Lazy import — keep discovery usable without crypto stack for URL-only helpers.
    from windows_os_api.apps.trust.signing import (
        _DEV_SECRET,
        _b64url_decode,
        _dev_secret,
        _parse_signature,
    )
    from windows_os_api.apps.trust.keystore import TrustKeystoreError, get_keystore
    from cryptography.exceptions import InvalidSignature

    try:
        alg, material, key_id = _parse_signature(sig)
    except TrustKeystoreError as exc:
        raise ReleaseDiscoveryError(f"release signature rejected: {exc}") from exc

    if alg == "hmac-dev":
        import hmac
        import hashlib

        expected = hmac.new(_dev_secret(), digest, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, material.lower()):
            raise ReleaseDiscoveryError("release signature invalid (hmac-dev)")
        return

    if alg == "ed25519":
        assert key_id is not None
        try:
            key = get_keystore().require_active(key_id)
            sig_bytes = _b64url_decode(material)
            key.public_key().verify(sig_bytes, digest)
        except (TrustKeystoreError, InvalidSignature, ValueError) as exc:
            raise ReleaseDiscoveryError(
                "release signature invalid (ed25519)"
            ) from exc
        return

    raise ReleaseDiscoveryError(f"unsupported release signature algorithm: {alg}")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """N031: never auto-follow redirects — validate Location without connecting."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        loc = headers.get("Location") or headers.get("location") or newurl
        # Validate the redirect target *before* any connection to it.
        try:
            validate_https_url(str(loc))
        except ReleaseDiscoveryError as exc:
            raise ReleaseDiscoveryError(
                f"redirect blocked before connect: {exc}"
            ) from exc
        raise ReleaseDiscoveryError(
            f"HTTP redirect denied (HTTP {code} → {loc})"
        )


def _fetch_bytes(url: str, *, max_bytes: int, timeout: float = 30.0) -> bytes:
    validate_https_url(url)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "WinOs-Layer-updater/N031", "Accept": "application/json,application/octet-stream"},
        method="GET",
    )
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler)
        with opener.open(req, timeout=timeout) as resp:  # noqa: S310 — validated https
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
    # N031: signature required + verified over sha256 (fail-closed).
    try:
        verify_release_signature(sha256_hex=actual, signature=release.signature)
    except ReleaseDiscoveryError:
        try:
            dest.unlink()
        except OSError:
            pass
        raise
    # Sidecar manifest for the staged artifact (N028)
    try:
        write_checksum_manifest([dest], staging_dir / "checksums.txt", root=staging_dir)
    except ChecksumManifestError as exc:
        raise ReleaseDiscoveryError(str(exc)) from exc
    return dest
