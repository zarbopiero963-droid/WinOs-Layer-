"""Reliable checksum manifests for release artifacts and update packages (N028).

Contract (H63-N028 / R44 R45 G05):
- Never list the manifest path in its own file contents (no self-hash / stale).
- Unique path labels; duplicate basenames are rejected (fail-closed).
- Missing files fail at generation and at independent verify.
- Altered files fail independent verify.
- Atomic write; regenerate-twice is byte-identical for the same inputs.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable

# Common manifest basenames that must never be hashed into themselves.
MANIFEST_BASENAMES = frozenset(
    {
        "checksums.txt",
        "checksums-linux.txt",
        "SHA256SUMS.txt",
        "SHA256SUMS",
        "sha256sums.txt",
    }
)

_LINE_RE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+)$")


class ChecksumManifestError(ValueError):
    """Fail-closed checksum manifest error (missing / duplicate / self / altered)."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_file(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ChecksumManifestError(f"checksum input missing or not a file: {path}")
    return resolved


def _label_for(path: Path, *, root: Path | None) -> str:
    """Stable relative label when under root; otherwise basename (must stay unique)."""
    if root is not None:
        try:
            rel = path.resolve().relative_to(root.resolve())
            return rel.as_posix()
        except ValueError:
            pass
    return path.name


def _is_manifest_path(path: Path, out: Path | None) -> bool:
    if out is not None:
        try:
            if path.expanduser().resolve() == out.expanduser().resolve():
                return True
        except OSError:
            if path.name == out.name:
                return True
    return path.name in MANIFEST_BASENAMES


def build_checksum_lines(
    paths: Iterable[Path],
    *,
    out: Path | None = None,
    root: Path | None = None,
) -> list[str]:
    """Return sorted ``sha256  label`` lines; never includes ``out`` / manifest names."""
    resolved: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if _is_manifest_path(p, out):
            continue
        resolved.append(_resolve_file(p))

    unique: dict[Path, Path] = {}
    for p in resolved:
        unique[p] = p

    entries: list[tuple[str, Path]] = []
    seen_basenames: dict[str, Path] = {}
    seen_labels: set[str] = set()
    for p in unique.values():
        label = _label_for(p, root=root)
        base = Path(label).name
        if base in seen_basenames and seen_basenames[base] != p:
            raise ChecksumManifestError(
                f"duplicate basename {base!r}: {seen_basenames[base]} vs {p}"
            )
        if label in seen_labels:
            raise ChecksumManifestError(f"duplicate path label {label!r}")
        seen_basenames[base] = p
        seen_labels.add(label)
        entries.append((label, p))

    entries.sort(key=lambda t: t[0])
    return [f"{sha256_file(p)}  {label}" for label, p in entries]


def write_checksum_manifest(
    paths: Iterable[Path],
    out: Path,
    *,
    root: Path | None = None,
) -> Path:
    """Atomically write a checksum manifest; excludes ``out`` even if listed in paths."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = build_checksum_lines(paths, out=out, root=root)
    payload = "\n".join(lines) + ("\n" if lines else "")
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{out.name}.",
        suffix=".tmp",
        dir=str(out.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, out)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return out


def parse_checksum_manifest(text: str) -> dict[str, str]:
    """Parse manifest text → label → sha256 (lower). Rejects duplicate basenames/labels."""
    mapping: dict[str, str] = {}
    seen_basenames: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            raise ChecksumManifestError(f"invalid checksum line: {line!r}")
        digest = m.group(1).lower()
        label = m.group(2).strip()
        if not label or label in (".", ".."):
            raise ChecksumManifestError(f"invalid checksum label: {label!r}")
        if label.startswith("/") or (len(label) > 1 and label[1] == ":"):
            raise ChecksumManifestError(f"absolute path label not allowed: {label!r}")
        base = Path(label).name
        if base in MANIFEST_BASENAMES or label in MANIFEST_BASENAMES:
            raise ChecksumManifestError(f"manifest must not list itself: {label!r}")
        if base in seen_basenames and seen_basenames[base] != label:
            raise ChecksumManifestError(
                f"duplicate basename in manifest: {base!r} "
                f"({seen_basenames[base]!r} vs {label!r})"
            )
        if label in mapping and mapping[label] != digest:
            raise ChecksumManifestError(f"duplicate conflicting label: {label!r}")
        seen_basenames[base] = label
        mapping[label] = digest
    return mapping


def verify_checksum_manifest(
    manifest_path: Path,
    *,
    root: Path | None = None,
) -> dict[str, str]:
    """Independent client: fail-closed on missing / altered / dup / self-hash entries.

    Returns the verified label→digest map on success.
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        raise ChecksumManifestError(f"checksum manifest missing: {manifest_path}")
    text = manifest_path.read_text(encoding="utf-8")
    mapping = parse_checksum_manifest(text)
    if not mapping:
        raise ChecksumManifestError(f"checksum manifest empty: {manifest_path}")

    base_root = root if root is not None else manifest_path.parent
    base_root_res = base_root.resolve()
    verified: dict[str, str] = {}
    for label, expected in mapping.items():
        candidate = Path(label)
        if candidate.is_absolute():
            raise ChecksumManifestError(f"absolute path label not allowed: {label!r}")
        target = (base_root / candidate).resolve()
        try:
            target.relative_to(base_root_res)
        except ValueError as exc:
            raise ChecksumManifestError(
                f"checksum path escapes root: {label!r}"
            ) from exc
        if target == manifest_path.resolve():
            raise ChecksumManifestError(f"manifest must not list itself: {label!r}")
        if not target.is_file():
            raise ChecksumManifestError(f"checksum target missing: {label}")
        actual = sha256_file(target)
        if actual != expected:
            raise ChecksumManifestError(
                f"checksum mismatch for {label}: expected {expected}, got {actual}"
            )
        verified[label] = actual
    return verified


def find_package_manifest(package_dir: Path) -> Path | None:
    """Return the preferred checksums manifest inside a package directory, if any."""
    package_dir = Path(package_dir)
    for name in (
        "checksums.txt",
        "checksums-linux.txt",
        "SHA256SUMS.txt",
        "SHA256SUMS",
    ):
        candidate = package_dir / name
        if candidate.is_file():
            return candidate
    return None
