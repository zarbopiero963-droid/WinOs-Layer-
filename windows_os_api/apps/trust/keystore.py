"""Trusted publisher keystore for production adapter signatures (N029).

Ed25519 public keys are identified by ``key_id`` and bound to a ``publisher``
string. Revoked keys never grant verified/publisher trust. HMAC-dev is not a
production trust root.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization


class TrustKeystoreError(ValueError):
    """Fail-closed trust keystore error."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class TrustedKey:
    key_id: str
    publisher: str
    public_key_raw: bytes  # 32-byte Ed25519 public key
    revoked_at: str | None = None
    created_at: str = field(default_factory=_utcnow)
    note: str = ""

    @property
    def revoked(self) -> bool:
        return bool(self.revoked_at)

    def public_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(self.public_key_raw)

    def to_json(self) -> dict[str, Any]:
        return {
            "key_id": self.key_id,
            "publisher": self.publisher,
            "public_key_hex": self.public_key_raw.hex(),
            "revoked_at": self.revoked_at,
            "created_at": self.created_at,
            "note": self.note,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "TrustedKey":
        kid = str(data.get("key_id") or "").strip()
        publisher = str(data.get("publisher") or "").strip()
        hx = str(data.get("public_key_hex") or "").strip()
        if not kid or not publisher or len(hx) != 64:
            raise TrustKeystoreError("invalid trusted key record")
        try:
            raw = bytes.fromhex(hx)
        except ValueError as exc:
            raise TrustKeystoreError("invalid public_key_hex") from exc
        if len(raw) != 32:
            raise TrustKeystoreError("Ed25519 public key must be 32 bytes")
        return cls(
            key_id=kid,
            publisher=publisher,
            public_key_raw=raw,
            revoked_at=data.get("revoked_at"),
            created_at=str(data.get("created_at") or _utcnow()),
            note=str(data.get("note") or ""),
        )


def _default_store_path() -> Path:
    env = os.environ.get("WINOS_TRUST_KEYSTORE")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("WINOS_DATA_DIR")
    if base:
        return Path(base).expanduser() / "trust" / "keystore.json"
    return Path.home() / ".winos" / "trust" / "keystore.json"


class TrustKeystore:
    """In-process + optional on-disk store of trusted Ed25519 publisher keys."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else _default_store_path()
        self._lock = threading.RLock()
        self._keys: dict[str, TrustedKey] = {}
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self._keys = {}
            if self.path.is_file():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise TrustKeystoreError(f"keystore unreadable: {exc}") from exc
                records = data.get("keys") if isinstance(data, dict) else None
                if not isinstance(records, list):
                    raise TrustKeystoreError("keystore missing keys list")
                for item in records:
                    if not isinstance(item, dict):
                        continue
                    key = TrustedKey.from_json(item)
                    self._keys[key.key_id] = key

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "keys": [k.to_json() for k in sorted(self._keys.values(), key=lambda x: x.key_id)],
            }
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def list_keys(self, *, include_revoked: bool = True) -> list[TrustedKey]:
        with self._lock:
            keys = list(self._keys.values())
        if not include_revoked:
            keys = [k for k in keys if not k.revoked]
        return sorted(keys, key=lambda k: k.key_id)

    def get(self, key_id: str) -> TrustedKey | None:
        with self._lock:
            return self._keys.get(key_id)

    def add_public_key(
        self,
        *,
        key_id: str,
        publisher: str,
        public_key_raw: bytes,
        note: str = "",
        persist: bool = True,
    ) -> TrustedKey:
        key_id = key_id.strip()
        publisher = publisher.strip()
        if not key_id or not publisher:
            raise TrustKeystoreError("key_id and publisher required")
        if len(public_key_raw) != 32:
            raise TrustKeystoreError("Ed25519 public key must be 32 bytes")
        # Validate parseable
        Ed25519PublicKey.from_public_bytes(public_key_raw)
        key = TrustedKey(
            key_id=key_id,
            publisher=publisher,
            public_key_raw=public_key_raw,
            note=note,
        )
        with self._lock:
            existing = self._keys.get(key_id)
            if existing and not existing.revoked:
                raise TrustKeystoreError(f"key_id already present: {key_id}")
            self._keys[key_id] = key
            if persist:
                self.save()
        return key

    def revoke(self, key_id: str, *, persist: bool = True) -> TrustedKey:
        with self._lock:
            key = self._keys.get(key_id)
            if key is None:
                raise TrustKeystoreError(f"unknown key_id: {key_id}")
            if not key.revoked_at:
                key.revoked_at = _utcnow()
            if persist:
                self.save()
            return key

    def require_active(self, key_id: str) -> TrustedKey:
        key = self.get(key_id)
        if key is None:
            raise TrustKeystoreError(f"unknown key_id: {key_id}")
        if key.revoked:
            raise TrustKeystoreError(f"key revoked: {key_id}")
        return key


_STORE: TrustKeystore | None = None
_STORE_LOCK = threading.Lock()


def get_keystore() -> TrustKeystore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = TrustKeystore()
        return _STORE


def reset_keystore_for_tests(store: TrustKeystore | None = None) -> TrustKeystore:
    """Test helper: replace process-global keystore."""
    global _STORE
    with _STORE_LOCK:
        _STORE = store if store is not None else TrustKeystore(path=Path(os.devnull))
        # Empty in-memory store when using /dev/null path — force empty
        if store is None:
            _STORE = TrustKeystore.__new__(TrustKeystore)
            _STORE.path = Path("/tmp/winos-trust-empty-do-not-use")
            _STORE._lock = threading.RLock()
            _STORE._keys = {}
        return _STORE


def generate_ed25519_keypair() -> tuple[Ed25519PrivateKey, bytes]:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv, pub
