"""Signed adapter trust levels."""
from __future__ import annotations
import hashlib
import hmac
import json
from typing import Any

TRUST_LEVELS = ("unsigned", "dev", "verified", "publisher")

# Dev HMAC secret — override via env in production
_DEV_SECRET = b"winos-adapter-dev-secret"

def sign_adapter_manifest(manifest: dict[str, Any], secret: bytes = _DEV_SECRET) -> str:
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()

def verify_adapter_signature(manifest: dict[str, Any], signature: str, secret: bytes = _DEV_SECRET) -> bool:
    expected = sign_adapter_manifest(manifest, secret)
    return hmac.compare_digest(expected, signature)

def resolve_trust_level(manifest: dict[str, Any], signature: str | None) -> str:
    if not signature:
        return "unsigned"
    if verify_adapter_signature(manifest, signature):
        publisher = manifest.get("publisher")
        if publisher and manifest.get("verified_publisher"):
            return "publisher"
        return "verified"
    return "unsigned"

def trust_allows(level: str, required: str) -> bool:
    order = {t: i for i, t in enumerate(TRUST_LEVELS)}
    return order.get(level, 0) >= order.get(required, 0)
