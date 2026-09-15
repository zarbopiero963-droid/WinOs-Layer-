"""N037 — Release attestation / publisher binding for EXE+Setup artifacts.

Builds a fail-closed release manifest: version, publisher, per-artifact sha256
and signing status (``unsigned`` | ``authenticode`` | ``attested``), optional
signer subject/thumbprint, and ``generated_at``.

An optional Ed25519 key (env ``WINOS_RELEASE_SIGNING_KEY``) signs the
attestation document for publisher binding. Without a key the attestation is
still written but ``attestation_signature`` is absent — verify treats that as
unsigned-attestation (never fake-signed).

Never mark ``authenticode`` / ``signed`` without verification evidence.
Checksum self-hash rules from N028 still apply to separate checksum manifests;
this module lists artifact hashes inside the attestation JSON (not a
SHA256SUMS self-hash).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization

from windows_os_api.update.checksum_manifest import sha256_file

ATTESTATION_SCHEMA_VERSION = 1
ATTESTATION_FILENAME = "RELEASE_ATTESTATION.json"
SIGNING_STATUSES = frozenset({"unsigned", "authenticode", "attested"})
_ED25519_PREFIX = "ed25519:"
_SKIP_SIGN_FIELDS = frozenset({"attestation_signature"})


class ReleaseAttestationError(ValueError):
    """Fail-closed release attestation / verify error."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_bytes(doc: Mapping[str, Any]) -> bytes:
    """Canonical JSON for Ed25519 (excludes attestation_signature)."""
    body = {k: v for k, v in doc.items() if k not in _SKIP_SIGN_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(token: str) -> bytes:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad)


def load_release_private_key(
    material: str | bytes | None = None,
) -> Ed25519PrivateKey | None:
    """Load Ed25519 private key from env or explicit material.

    Accepted forms (env ``WINOS_RELEASE_SIGNING_KEY``):
    - 64-char hex of 32 raw private bytes
    - base64 / base64url of 32 raw private bytes
    - path to a PEM or raw 32-byte file (``WINOS_RELEASE_SIGNING_KEY_PATH``)
    - PEM string starting with ``-----BEGIN``
    """
    raw_env = material
    if raw_env is None:
        path_env = os.environ.get("WINOS_RELEASE_SIGNING_KEY_PATH", "").strip()
        if path_env:
            p = Path(path_env).expanduser()
            if not p.is_file():
                raise ReleaseAttestationError(f"release signing key path missing: {p}")
            raw_env = p.read_bytes()
        else:
            raw_env = os.environ.get("WINOS_RELEASE_SIGNING_KEY")
    if raw_env is None or raw_env == "":
        return None
    if isinstance(raw_env, bytes):
        data: bytes | str = raw_env
    else:
        data = raw_env.strip()
    if isinstance(data, str) and data.startswith("-----BEGIN"):
        return serialization.load_pem_private_key(data.encode("utf-8"), password=None)  # type: ignore[return-value]
    if isinstance(data, bytes) and data.startswith(b"-----BEGIN"):
        return serialization.load_pem_private_key(data, password=None)  # type: ignore[return-value]
    if isinstance(data, bytes) and len(data) == 32:
        return Ed25519PrivateKey.from_private_bytes(data)
    if isinstance(data, str):
        # hex
        if len(data) == 64 and all(c in "0123456789abcdefABCDEF" for c in data):
            return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(data))
        # maybe path
        maybe = Path(data).expanduser()
        if maybe.is_file():
            blob = maybe.read_bytes()
            if blob.startswith(b"-----BEGIN"):
                return serialization.load_pem_private_key(blob, password=None)  # type: ignore[return-value]
            if len(blob) == 32:
                return Ed25519PrivateKey.from_private_bytes(blob)
            # try PEM via decode
            try:
                return serialization.load_pem_private_key(blob, password=None)  # type: ignore[return-value]
            except Exception:
                pass
        # base64 / base64url
        try:
            decoded = _b64url_decode(data.replace("+", "-").replace("/", "_"))
            if len(decoded) == 32:
                return Ed25519PrivateKey.from_private_bytes(decoded)
        except Exception as exc:
            raise ReleaseAttestationError("unrecognized WINOS_RELEASE_SIGNING_KEY material") from exc
    raise ReleaseAttestationError("unrecognized WINOS_RELEASE_SIGNING_KEY material")


def public_key_hex(private_key: Ed25519PrivateKey) -> str:
    pub = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return pub.hex()


def load_release_public_key(
    material: str | bytes | None = None,
) -> Ed25519PublicKey | None:
    """Load Ed25519 public key from env ``WINOS_RELEASE_VERIFY_KEY`` (hex/b64/PEM/path)."""
    raw = material
    if raw is None:
        path_env = os.environ.get("WINOS_RELEASE_VERIFY_KEY_PATH", "").strip()
        if path_env:
            p = Path(path_env).expanduser()
            if not p.is_file():
                raise ReleaseAttestationError(f"release verify key path missing: {p}")
            raw = p.read_bytes()
        else:
            raw = os.environ.get("WINOS_RELEASE_VERIFY_KEY")
    if raw is None or raw == "":
        return None
    if isinstance(raw, bytes):
        data: bytes | str = raw
    else:
        data = raw.strip()
    if isinstance(data, str) and data.startswith("-----BEGIN"):
        return serialization.load_pem_public_key(data.encode("utf-8"))  # type: ignore[return-value]
    if isinstance(data, bytes) and data.startswith(b"-----BEGIN"):
        return serialization.load_pem_public_key(data)  # type: ignore[return-value]
    if isinstance(data, bytes) and len(data) == 32:
        return Ed25519PublicKey.from_public_bytes(data)
    if isinstance(data, str):
        if len(data) == 64 and all(c in "0123456789abcdefABCDEF" for c in data):
            return Ed25519PublicKey.from_public_bytes(bytes.fromhex(data))
        maybe = Path(data).expanduser()
        if maybe.is_file():
            blob = maybe.read_bytes()
            if blob.startswith(b"-----BEGIN"):
                return serialization.load_pem_public_key(blob)  # type: ignore[return-value]
            if len(blob) == 32:
                return Ed25519PublicKey.from_public_bytes(blob)
        try:
            decoded = _b64url_decode(data.replace("+", "-").replace("/", "_"))
            if len(decoded) == 32:
                return Ed25519PublicKey.from_public_bytes(decoded)
        except Exception as exc:
            raise ReleaseAttestationError("unrecognized WINOS_RELEASE_VERIFY_KEY material") from exc
    raise ReleaseAttestationError("unrecognized WINOS_RELEASE_VERIFY_KEY material")


def default_publisher() -> str:
    return (os.environ.get("WINOS_RELEASE_PUBLISHER") or "WinOs-Layer").strip() or "WinOs-Layer"


def normalize_signing_status(status: str | None) -> str:
    s = (status or "unsigned").strip().lower()
    if s in {"signed", "signtool"}:
        # Fail-closed aliases: never invent authenticode from vague "signed"
        raise ReleaseAttestationError(
            "ambiguous signing status 'signed' — use unsigned|authenticode|attested"
        )
    if s not in SIGNING_STATUSES:
        raise ReleaseAttestationError(f"invalid signing_status: {status!r}")
    return s


def artifact_entry(
    path: Path,
    *,
    signing_status: str = "unsigned",
    signer_subject: str | None = None,
    signer_thumbprint: str | None = None,
    label: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Build one artifact record (sha256 of file bytes)."""
    path = Path(path)
    if not path.is_file():
        raise ReleaseAttestationError(f"missing artifact: {path}")
    status = normalize_signing_status(signing_status)
    if status == "authenticode" and not (signer_subject or signer_thumbprint):
        # Evidence required: at least one of subject/thumbprint after real sign,
        # or explicit pe_presence marker via signer_subject="pe-signature-present"
        # — callers that set authenticode after signtool should pass evidence.
        pass
    if label:
        name = label
    elif root is not None:
        try:
            name = str(path.resolve().relative_to(Path(root).resolve())).replace("\\", "/")
        except ValueError:
            name = path.name
    else:
        name = path.name
    entry: dict[str, Any] = {
        "name": name,
        "sha256": sha256_file(path),
        "signing_status": status,
        "size": path.stat().st_size,
    }
    if signer_subject:
        entry["signer_subject"] = signer_subject
    if signer_thumbprint:
        entry["signer_thumbprint"] = signer_thumbprint
    return entry


def build_attestation(
    *,
    version: str,
    publisher: str,
    artifacts: Sequence[Mapping[str, Any]],
    generated_at: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble attestation document (no signature yet)."""
    pub = (publisher or "").strip()
    if not pub:
        raise ReleaseAttestationError("publisher required")
    ver = (version or "").strip()
    if not ver:
        raise ReleaseAttestationError("version required")
    arts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in artifacts:
        if not isinstance(raw, Mapping):
            raise ReleaseAttestationError("artifact must be a mapping")
        name = str(raw.get("name") or "").strip()
        digest = str(raw.get("sha256") or "").strip().lower()
        status = normalize_signing_status(str(raw.get("signing_status") or "unsigned"))
        if not name or len(digest) != 64:
            raise ReleaseAttestationError(f"invalid artifact record: {raw!r}")
        if name in seen:
            raise ReleaseAttestationError(f"duplicate artifact name: {name}")
        seen.add(name)
        # Fail-closed: never allow status authenticode without evidence fields
        # when the caller tries to claim it via a bare flag with no digests —
        # evidence can be subject/thumbprint OR pe_sig_present bool.
        if status == "authenticode":
            has_evidence = bool(
                raw.get("signer_subject")
                or raw.get("signer_thumbprint")
                or raw.get("pe_sig_present")
                or raw.get("authenticode_verified")
            )
            if not has_evidence:
                raise ReleaseAttestationError(
                    f"authenticode claim for {name!r} requires signer evidence "
                    "(subject/thumbprint/pe_sig_present/authenticode_verified)"
                )
        item: dict[str, Any] = {
            "name": name,
            "sha256": digest,
            "signing_status": status,
        }
        if "size" in raw and raw["size"] is not None:
            item["size"] = int(raw["size"])
        for opt in (
            "signer_subject",
            "signer_thumbprint",
            "pe_sig_present",
            "authenticode_verified",
        ):
            if opt in raw and raw[opt] is not None:
                item[opt] = raw[opt]
        arts.append(item)
    if not arts:
        raise ReleaseAttestationError("at least one artifact required")
    doc: dict[str, Any] = {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "version": ver,
        "publisher": pub,
        "generated_at": generated_at or _utcnow(),
        "artifacts": arts,
    }
    if extra:
        for k, v in extra.items():
            if k in doc or k in _SKIP_SIGN_FIELDS:
                continue
            doc[k] = v
    return doc


def sign_attestation(
    doc: Mapping[str, Any],
    private_key: Ed25519PrivateKey,
    *,
    key_id: str = "release",
) -> str:
    """Return ``ed25519:<key_id>:<base64url(sig)>`` over canonical attestation body."""
    kid = (key_id or "release").strip()
    if not kid or ":" in kid:
        raise ReleaseAttestationError("invalid key_id")
    sig = private_key.sign(_canonical_bytes(doc))
    return f"{_ED25519_PREFIX}{kid}:{_b64url_encode(sig)}"


def verify_attestation_signature(
    doc: Mapping[str, Any],
    signature: str | None,
    public_key: Ed25519PublicKey,
) -> bool:
    """Cryptographic check only (does not enforce publisher string)."""
    if not signature:
        return False
    sig = signature.strip()
    if not sig.startswith(_ED25519_PREFIX):
        return False
    rest = sig[len(_ED25519_PREFIX) :]
    if ":" not in rest:
        return False
    _kid, token = rest.split(":", 1)
    try:
        public_key.verify(_b64url_decode(token), _canonical_bytes(doc))
        return True
    except (InvalidSignature, ValueError):
        return False


def attach_signature(
    doc: dict[str, Any],
    private_key: Ed25519PrivateKey | None,
    *,
    key_id: str = "release",
) -> dict[str, Any]:
    """Return a copy with optional attestation_signature + public key hex."""
    out = dict(doc)
    if private_key is None:
        out.pop("attestation_signature", None)
        out.pop("attestation_public_key_hex", None)
        return out
    out["attestation_public_key_hex"] = public_key_hex(private_key)
    # Sign without the signature field; include public key hex in signed body
    # so verifiers can pin the key that was intended at generation time.
    sig = sign_attestation(out, private_key, key_id=key_id)
    out["attestation_signature"] = sig
    return out


def write_attestation(
    doc: Mapping[str, Any],
    path: Path,
    *,
    private_key: Ed25519PrivateKey | None = None,
    key_id: str = "release",
) -> Path:
    """Write attestation JSON (optionally signed). Atomic replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = attach_signature(dict(doc), private_key, key_id=key_id)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_attestation(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise ReleaseAttestationError(f"attestation missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseAttestationError(f"attestation unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise ReleaseAttestationError("attestation root must be object")
    return data


def pe_has_authenticode_blob(path: Path) -> bool | None:
    """Pure-Python check: PE Certificate Table present (presence only).

    Returns True/False when the file looks like a PE; None if not a PE /
    unreadable. This is **not** a cryptographic Authenticode or SmartScreen
    verification — never treat True as installed-product PASS.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 64 or data[:2] != b"MZ":
        return None
    (e_lfanew,) = struct.unpack_from("<I", data, 0x3C)
    if e_lfanew + 24 > len(data) or data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        return None
    # COFF header
    machine_off = e_lfanew + 4
    (num_sections,) = struct.unpack_from("<H", data, machine_off + 2)
    (optional_magic,) = struct.unpack_from("<H", data, machine_off + 20)
    if optional_magic == 0x10B:  # PE32
        dd_offset = machine_off + 20 + 96
    elif optional_magic == 0x20B:  # PE32+
        dd_offset = machine_off + 20 + 112
    else:
        return None
    # Certificate Table is data directory index 4
    cert_dir_off = dd_offset + 4 * 8
    if cert_dir_off + 8 > len(data):
        return None
    rva, size = struct.unpack_from("<II", data, cert_dir_off)
    _ = num_sections  # reserved for future section walk
    return bool(rva and size)


def try_signtool_verify(path: Path) -> dict[str, Any]:
    """On Windows, attempt ``signtool verify /pa``. Else unavailable."""
    path = Path(path)
    report: dict[str, Any] = {
        "pe_verify": "unavailable",
        "ok": None,
        "detail": None,
    }
    if sys.platform != "win32":
        presence = pe_has_authenticode_blob(path)
        if presence is True:
            report["pe_verify"] = "presence_only"
            report["ok"] = True
            report["detail"] = "PE certificate table present (not cryptographic verify)"
        elif presence is False:
            report["pe_verify"] = "presence_only"
            report["ok"] = False
            report["detail"] = "PE has no certificate table"
        else:
            report["pe_verify"] = "unavailable"
            report["detail"] = "not a PE or unreadable; Authenticode crypto verify N/A on this host"
        return report
    signtool = _find_signtool()
    if not signtool:
        presence = pe_has_authenticode_blob(path)
        report["pe_verify"] = "presence_only" if presence is not None else "unavailable"
        report["ok"] = presence
        report["detail"] = "signtool not found; fell back to PE presence check"
        return report
    try:
        proc = subprocess.run(
            [signtool, "verify", "/pa", "/v", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        report["detail"] = f"signtool failed: {exc}"
        return report
    report["pe_verify"] = "signtool"
    report["ok"] = proc.returncode == 0
    report["detail"] = (proc.stdout or "")[-2000:] + (proc.stderr or "")[-1000:]
    return report


def _find_signtool() -> str | None:
    env = os.environ.get("WINOS_SIGNTOOL", "").strip()
    if env and Path(env).is_file():
        return env
    which = __import__("shutil").which("signtool")
    if which:
        return which
    # Common Kit paths (best-effort)
    roots = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Windows Kits"
        / "10"
        / "bin",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Windows Kits"
        / "10"
        / "bin",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for cand in sorted(root.glob("*/x64/signtool.exe"), reverse=True):
            if cand.is_file():
                return str(cand)
    return None


def verify_release_artifacts(
    attestation_path: Path,
    *,
    root: Path | None = None,
    expected_publisher: str | None = None,
    public_key: Ed25519PublicKey | None = None,
    require_attestation_signature: bool = False,
    require_authenticode: bool = False,
) -> dict[str, Any]:
    """Independent verify client for RELEASE_ATTESTATION.json.

    - Recomputes sha256; mismatch → fail.
    - Unexpected publisher / bad attestation signature / tampered file → fail.
    - Authenticode claims: Windows tries signtool; Linux reports
      ``pe_verify=unavailable`` or ``presence_only`` — never invents SmartScreen PASS.
    """
    att_path = Path(attestation_path)
    doc = load_attestation(att_path)
    base = Path(root) if root is not None else att_path.parent
    errors: list[str] = []
    warnings: list[str] = []
    artifact_reports: list[dict[str, Any]] = []

    publisher = str(doc.get("publisher") or "").strip()
    if expected_publisher is not None:
        exp = expected_publisher.strip()
        if publisher != exp:
            errors.append(f"unexpected publisher: have {publisher!r}, expected {exp!r}")

    sig = doc.get("attestation_signature")
    pub_hex = doc.get("attestation_public_key_hex")
    pk = public_key
    if pk is None and isinstance(pub_hex, str) and len(pub_hex) == 64:
        try:
            pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        except ValueError:
            errors.append("attestation_public_key_hex invalid")
    if require_attestation_signature and not sig:
        errors.append("attestation_signature required but absent (unsigned-attestation)")
    if sig:
        if pk is None:
            errors.append("attestation_signature present but no public key to verify")
        elif not verify_attestation_signature(doc, str(sig), pk):
            errors.append("attestation signature verification failed")
    elif not require_attestation_signature:
        warnings.append("unsigned-attestation (no attestation_signature)")

    arts = doc.get("artifacts")
    if not isinstance(arts, list) or not arts:
        errors.append("attestation missing artifacts list")
        arts = []

    for raw in arts:
        if not isinstance(raw, Mapping):
            errors.append("artifact entry not an object")
            continue
        name = str(raw.get("name") or "")
        expected_hash = str(raw.get("sha256") or "").lower()
        status = str(raw.get("signing_status") or "unsigned")
        target = base / name
        if not target.is_file():
            # also try basename-only under root
            alt = base / Path(name).name
            target = alt if alt.is_file() else target
        rep: dict[str, Any] = {
            "name": name,
            "path": str(target),
            "signing_status": status,
            "hash_ok": False,
            "pe_verify": None,
        }
        if not target.is_file():
            errors.append(f"missing artifact file: {name}")
            artifact_reports.append(rep)
            continue
        actual = sha256_file(target)
        rep["sha256_actual"] = actual
        rep["sha256_expected"] = expected_hash
        if actual != expected_hash:
            errors.append(f"sha256 mismatch for {name}")
            artifact_reports.append(rep)
            continue
        rep["hash_ok"] = True

        if status == "authenticode":
            pe_rep = try_signtool_verify(target)
            rep["pe_verify"] = pe_rep.get("pe_verify")
            rep["pe_detail"] = pe_rep.get("detail")
            if pe_rep.get("pe_verify") == "signtool":
                if not pe_rep.get("ok"):
                    errors.append(f"Authenticode signtool verify failed for {name}")
            elif pe_rep.get("pe_verify") == "presence_only":
                if pe_rep.get("ok") is False:
                    errors.append(f"authenticode claimed but PE has no signature blob: {name}")
                else:
                    warnings.append(
                        f"{name}: pe_verify=presence_only (not cryptographic Authenticode PASS)"
                    )
            else:
                warnings.append(
                    f"{name}: pe_verify=unavailable — Authenticode not claimed PASS on this host"
                )
                if require_authenticode:
                    errors.append(
                        f"require_authenticode but pe_verify unavailable for {name}"
                    )
        elif status not in SIGNING_STATUSES:
            errors.append(f"invalid signing_status for {name}: {status}")
        elif require_authenticode and status != "authenticode":
            errors.append(f"require_authenticode but {name} is {status}")

        artifact_reports.append(rep)

    ok = not errors
    return {
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
        "publisher": publisher,
        "version": doc.get("version"),
        "attestation_signed": bool(sig),
        "artifacts": artifact_reports,
        "attestation_path": str(att_path),
    }


def collect_release_artifacts(
    dirs: Iterable[Path],
    *,
    suffixes: Sequence[str] = (".exe", ".msi", ".msix", ".zip", ".deb", ".rpm", ".tgz"),
) -> list[Path]:
    """List release candidates under dist/ and installer/output/."""
    found: list[Path] = []
    seen: set[Path] = set()
    want = {s.lower() for s in suffixes}
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.name == ATTESTATION_FILENAME:
                continue
            suf = p.suffix.lower()
            ok = suf in want or p.name.endswith(".tar.gz") or p.name.endswith(".AppImage")
            if not ok:
                continue
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            found.append(p)
    return found


def schema_helpers_present() -> dict[str, bool]:
    """For build_installer.validate — structural N037 checks (no real cert)."""
    return {
        "release_attestation_module": True,
        "build_attestation": callable(build_attestation),
        "verify_release_artifacts": callable(verify_release_artifacts),
        "write_attestation": callable(write_attestation),
        "signing_statuses": SIGNING_STATUSES == frozenset({"unsigned", "authenticode", "attested"}),
    }
