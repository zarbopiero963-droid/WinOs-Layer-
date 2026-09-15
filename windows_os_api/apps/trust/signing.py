"""Adapter trust signatures — HMAC-dev vs Ed25519 production (N029).

Production trust (`verified` / `publisher`) requires an Ed25519 signature over
the canonical manifest payload, verified against a **non-revoked** key in the
trust keystore. The publisher identity comes from the keystore record, never
from a self-asserted ``verified_publisher`` flag in the manifest.

HMAC with the built-in/dev secret can grant at most ``dev`` (never production
levels). Unknown / bad / revoked / forged-publisher → ``unsigned``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from windows_os_api.apps.trust.keystore import (
    TrustKeystoreError,
    get_keystore,
)

TRUST_LEVELS = ("unsigned", "dev", "verified", "publisher")

# Dev HMAC secret — intentionally public for local/dev only; never production root.
_DEV_SECRET = b"winos-adapter-dev-secret"
_HMAC_PREFIX = "hmac-dev:"
_ED25519_PREFIX = "ed25519:"


def _canonical_payload(manifest: dict[str, Any]) -> bytes:
    """Canonical JSON bytes. Trust/meta/ephemeral fields are excluded.

    Signed identity covers adapter content (app_id/actions/publisher/…) but never
    self-asserted trust fields or timestamps that would invalidate on reload.
    """
    skip = {
        "signature",
        "trust_level",
        "verified_publisher",  # never self-asserted into the signed identity
        "saved_at",
        "openapi",
        "_path",
        "persist_error",
        "persisted",
        "bound",
        "hwnd",
    }
    body = {k: v for k, v in manifest.items() if k not in skip}
    # Derived verification evidence must not bind the publisher signature (N030).
    actions = body.get("actions")
    if isinstance(actions, list):
        cleaned = []
        for item in actions:
            if not isinstance(item, dict):
                continue
            cleaned.append(
                {
                    k: v
                    for k, v in item.items()
                    if k != "verification"
                }
            )
        body["actions"] = cleaned
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _dev_secret() -> bytes:
    env = os.environ.get("WINOS_ADAPTER_HMAC_SECRET")
    if env:
        return env.encode("utf-8")
    return _DEV_SECRET


def sign_adapter_manifest_hmac(manifest: dict[str, Any], secret: bytes | None = None) -> str:
    """Legacy/dev HMAC signature (prefixed). Grants at most ``dev`` trust."""
    secret = secret if secret is not None else _dev_secret()
    digest = hmac.new(secret, _canonical_payload(manifest), hashlib.sha256).hexdigest()
    return f"{_HMAC_PREFIX}{digest}"


def sign_adapter_manifest_ed25519(
    manifest: dict[str, Any],
    *,
    key_id: str,
    private_key: Ed25519PrivateKey,
) -> str:
    """Production Ed25519 signature: ``ed25519:<key_id>:<base64url(sig)>``."""
    key_id = key_id.strip()
    if not key_id or ":" in key_id:
        raise TrustKeystoreError("invalid key_id for signature")
    sig = private_key.sign(_canonical_payload(manifest))
    token = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{_ED25519_PREFIX}{key_id}:{token}"


def sign_adapter_manifest(manifest: dict[str, Any], secret: bytes | None = None) -> str:
    """Backward-compatible helper: HMAC-dev signature (not production trust)."""
    return sign_adapter_manifest_hmac(manifest, secret=secret)


def _parse_signature(signature: str) -> tuple[str, str, str | None]:
    """Return (alg, material, key_id|None)."""
    signature = (signature or "").strip()
    if signature.startswith(_ED25519_PREFIX):
        rest = signature[len(_ED25519_PREFIX) :]
        if ":" not in rest:
            raise TrustKeystoreError("malformed ed25519 signature")
        key_id, token = rest.split(":", 1)
        return "ed25519", token, key_id
    if signature.startswith(_HMAC_PREFIX):
        return "hmac-dev", signature[len(_HMAC_PREFIX) :], None
    # Legacy bare hex HMAC (pre-N029) — treat as hmac-dev
    if len(signature) == 64 and all(c in "0123456789abcdefABCDEF" for c in signature):
        return "hmac-dev", signature.lower(), None
    raise TrustKeystoreError("unsupported signature algorithm")


def _b64url_decode(token: str) -> bytes:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad)


def verify_adapter_signature(
    manifest: dict[str, Any],
    signature: str,
    secret: bytes | None = None,
) -> bool:
    """Return True if the signature cryptographically verifies (any alg).

    Does **not** by itself mean production trust — use ``resolve_trust_level``.
    """
    try:
        alg, material, key_id = _parse_signature(signature)
    except TrustKeystoreError:
        return False
    payload = _canonical_payload(manifest)
    if alg == "hmac-dev":
        secret = secret if secret is not None else _dev_secret()
        return hmac.compare_digest(
            hmac.new(secret, payload, hashlib.sha256).hexdigest(),
            material.lower(),
        )
    if alg == "ed25519":
        assert key_id is not None
        try:
            key = get_keystore().require_active(key_id)
            sig_bytes = _b64url_decode(material)
            key.public_key().verify(sig_bytes, payload)
            # Provenance: manifest publisher, if present, must match key binding.
            claimed = manifest.get("publisher")
            return not (
                claimed is not None and str(claimed).strip() != key.publisher
            )
        except (TrustKeystoreError, InvalidSignature, ValueError):
            return False
    return False


def resolve_trust_level(manifest: dict[str, Any], signature: str | None) -> str:
    """Map signature + keystore provenance to a trust level.

    - no/invalid signature → ``unsigned``
    - valid HMAC-dev → ``dev`` (never verified/publisher)
    - valid Ed25519 + active key → ``verified``
    - valid Ed25519 + active key + manifest.publisher matches key.publisher → ``publisher``
    - revoked / unknown / forged publisher → ``unsigned``
    """
    if not signature:
        return "unsigned"
    try:
        alg, _material, key_id = _parse_signature(signature)
    except TrustKeystoreError:
        return "unsigned"

    if alg == "hmac-dev":
        if verify_adapter_signature(manifest, signature):
            return "dev"
        return "unsigned"

    if alg == "ed25519":
        assert key_id is not None
        try:
            key = get_keystore().require_active(key_id)
        except TrustKeystoreError:
            return "unsigned"
        if not verify_adapter_signature(manifest, signature):
            return "unsigned"
        claimed = manifest.get("publisher")
        if claimed is not None and str(claimed).strip() == key.publisher:
            return "publisher"
        # Signed by a trusted key but publisher omitted → verified (not publisher).
        if claimed is None or str(claimed).strip() == "":
            return "verified"
        # claimed publisher mismatched — verify_adapter_signature already False
        return "unsigned"

    return "unsigned"


def trust_allows(level: str, required: str) -> bool:
    order = {t: i for i, t in enumerate(TRUST_LEVELS)}
    return order.get(level, 0) >= order.get(required, 0)


def require_trust(
    manifest: dict[str, Any],
    signature: str | None,
    *,
    required: str = "verified",
) -> dict[str, Any]:
    """Fail-closed gate for load/execute when production trust is required."""
    level = resolve_trust_level(manifest, signature)
    allowed = trust_allows(level, required)
    return {
        "allowed": allowed,
        "trust_level": level,
        "required": required,
        "reason": None if allowed else f"trust insufficient: have {level}, need {required}",
    }


def adapter_signing_manifest(adapter: Any) -> dict[str, Any]:
    """Build the canonical signed body from a live Adapter (N030)."""
    actions = []
    for action in getattr(adapter, "actions", []) or []:
        item = {
            "name": getattr(action, "name", ""),
            "description": getattr(action, "description", ""),
            "automation_id": getattr(action, "automation_id", ""),
            "control_type": getattr(action, "control_type", ""),
            "params": list(getattr(action, "params", []) or []),
            "risk": getattr(action, "risk", "low"),
        }
        # verification evidence is *derived* — not part of publisher signature
        actions.append(item)
    body: dict[str, Any] = {
        "app_id": getattr(adapter, "app_id", ""),
        "app_name": getattr(adapter, "app_name", "") or getattr(adapter, "app_id", ""),
        "actions": actions,
    }
    publisher = getattr(adapter, "publisher", None)
    if publisher:
        body["publisher"] = publisher
    # Include manifest_version when present on adapter/store convention
    mv = getattr(adapter, "manifest_version", None)
    if mv is not None:
        body["manifest_version"] = mv
    return body


def revalidate_adapter_trust(adapter: Any, *, required: str = "verified") -> dict[str, Any]:
    """Re-verify signature over current adapter content (tamper → deny).

    Used before invoke (N030). Unsigned adapters without an ed25519/production
    claim remain allowed. Returns gate dict like ``require_trust``.
    """
    sig = getattr(adapter, "signature", None) or ""
    level = getattr(adapter, "trust_level", "unsigned") or "unsigned"
    needs = level in {"verified", "publisher"} or str(sig).startswith("ed25519:")
    if not needs:
        return {
            "allowed": True,
            "trust_level": level,
            "required": required,
            "reason": None,
            "tampered": False,
        }
    manifest = adapter_signing_manifest(adapter)
    # Prefer store manifest_version if actions were loaded from disk
    from windows_os_api.apps.adapters.store import MANIFEST_VERSION

    manifest.setdefault("manifest_version", MANIFEST_VERSION)
    crypto_ok = bool(sig) and verify_adapter_signature(manifest, sig)
    resolved = resolve_trust_level(manifest, sig if sig else None)
    allowed = crypto_ok and trust_allows(resolved, required)
    return {
        "allowed": allowed,
        "trust_level": resolved,
        "required": required,
        "reason": None
        if allowed
        else (
            "adapter content no longer matches signature (tamper or revoked key)"
            if not crypto_ok
            else f"trust insufficient: have {resolved}, need {required}"
        ),
        "tampered": not crypto_ok,
        "resolved": resolved,
    }

