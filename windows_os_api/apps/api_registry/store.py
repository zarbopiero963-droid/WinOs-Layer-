"""API Registry persistence: versioned, crash-safe store (N015 / H63-N015).

Mirrors ``adapters.store``: write tmp → atomic rename; fail-closed on
unreadable / wrong version / malformed; report skips. Restart between
processes keeps deterministic ``id`` identity.

Hard rules
----------
* Truncated / corrupt primary does **not** publish executable APIs; try ``.bak``.
* Both bad → empty registry + reported error codes (no silent fake VERIFIED).
* Wrong ``schema_version`` → fail-closed (do not interpret unknown format).
* Persisted ``status`` is never trusted alone: every record is re-registered
  through ``ApiRegistry.register`` / ``authorize_verified_status``.
* Id that disagrees with the natural key is rejected (not published).
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from windows_os_api.apps.api_registry.model import (
    API_REGISTRY_SCHEMA_VERSION,
    ApiRecord,
    ApiRegistry,
    RegistrationRejected,
    compute_api_id,
)

ENV_VAR = "WINOS_API_REGISTRY_STORE"
STORE_FILENAME = "api_registry.json"
STORE_BAK_FILENAME = "api_registry.json.bak"

# File-format version (integer). Bump when field meanings change.
STORE_FORMAT_VERSION = 1

STORE_UNREADABLE = "STORE_UNREADABLE"
STORE_VERSION_UNKNOWN = "STORE_VERSION_UNKNOWN"
STORE_MALFORMED = "STORE_MALFORMED"
STORE_MISSING = "STORE_MISSING"
STORE_RECOVERED_FROM_BAK = "STORE_RECOVERED_FROM_BAK"
RECORD_REJECTED = "RECORD_REJECTED"
RECORD_ID_MISMATCH = "RECORD_ID_MISMATCH"
RECORD_COLLISION = "RECORD_COLLISION"


@dataclass(frozen=True)
class SkippedStore:
    """A store file or record that was not loaded, and why."""

    path: str
    code: str
    reason: str


@dataclass
class LoadReport:
    """Outcome of ``load_registry`` — always inspect ``skipped``."""

    registry: ApiRegistry
    skipped: list[SkippedStore] = field(default_factory=list)
    recovered_from_bak: bool = False
    source_path: str | None = None


def _user_config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "winos-api"


def store_dir() -> Path:
    """Where the registry file lives. ``WINOS_API_REGISTRY_STORE`` overrides.

    Read on every call (not cached) so tests can monkeypatch the env.
    """
    override = os.environ.get(ENV_VAR, "").strip()
    if override:
        return Path(override)
    return _user_config_dir() / "api_registry"


def store_path() -> Path:
    return store_dir() / STORE_FILENAME


def bak_path() -> Path:
    return store_dir() / STORE_BAK_FILENAME


def _records_to_payload(records: list[ApiRecord]) -> dict[str, Any]:
    return {
        "store_format_version": STORE_FORMAT_VERSION,
        "schema_version": API_REGISTRY_SCHEMA_VERSION,
        "saved_at": time.time(),
        "records": [r.to_dict() for r in records],
    }


def save_registry(registry: ApiRegistry) -> Path:
    """Atomically persist ``registry``; keep previous good file as ``.bak``.

    Write order: serialize → tmp → (move current good → bak) → rename tmp → primary.
    A crash mid-write leaves the previous primary (or bak) intact.
    """
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _records_to_payload(registry.list())
    text = json.dumps(payload, indent=2)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")

    if path.exists():
        bak = bak_path()
        try:
            path.replace(bak)
        except OSError:
            pass

    tmp.replace(path)
    return path


def _parse_store_file(path: Path) -> tuple[dict[str, Any] | None, SkippedStore | None]:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, SkippedStore(str(path), STORE_UNREADABLE, str(exc))

    if not raw_text.strip():
        return None, SkippedStore(str(path), STORE_UNREADABLE, "empty file")

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return None, SkippedStore(str(path), STORE_UNREADABLE, str(exc))

    if not isinstance(raw, dict):
        return None, SkippedStore(
            str(path), STORE_MALFORMED, "store root is not a JSON object"
        )

    # Fail-closed on unknown / missing format version.
    if "store_format_version" not in raw:
        return None, SkippedStore(
            str(path),
            STORE_VERSION_UNKNOWN,
            "missing store_format_version",
        )
    if raw["store_format_version"] != STORE_FORMAT_VERSION:
        return None, SkippedStore(
            str(path),
            STORE_VERSION_UNKNOWN,
            f"store_format_version {raw['store_format_version']!r}, "
            f"this runtime reads {STORE_FORMAT_VERSION}",
        )
    if "schema_version" in raw and str(raw["schema_version"]) != API_REGISTRY_SCHEMA_VERSION:
        return None, SkippedStore(
            str(path),
            STORE_VERSION_UNKNOWN,
            f"schema_version {raw['schema_version']!r}, "
            f"this runtime reads {API_REGISTRY_SCHEMA_VERSION}",
        )

    records = raw.get("records")
    if records is None:
        return None, SkippedStore(str(path), STORE_MALFORMED, "records missing")
    if not isinstance(records, list):
        return None, SkippedStore(str(path), STORE_MALFORMED, "records is not a list")

    return raw, None


def _ingest_records(
    registry: ApiRegistry,
    records: list[Any],
    *,
    source_path: str,
    skipped: list[SkippedStore],
) -> None:
    """Re-register each record; reject id/natural-key mismatch and collisions."""
    seen_ids: set[str] = set()
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            skipped.append(
                SkippedStore(
                    source_path,
                    RECORD_REJECTED,
                    f"record[{index}] is not an object",
                )
            )
            continue

        method = str(item.get("method") or "").strip().upper()
        path = str(item.get("path") or "").strip()
        application_id = str(item.get("application_id") or "").strip()
        capability = str(item.get("capability") or "").strip()
        source = str(item.get("source") or "").strip()
        expected_id = compute_api_id(
            method=method,
            path=path,
            application_id=application_id,
            capability=capability,
            source=source,
        )
        claimed = item.get("id")
        if claimed is not None and str(claimed).strip() and str(claimed).strip() != expected_id:
            skipped.append(
                SkippedStore(
                    source_path,
                    RECORD_ID_MISMATCH,
                    f"record[{index}] id {claimed!r} disagrees with natural key "
                    f"(expected {expected_id})",
                )
            )
            continue

        if expected_id in seen_ids:
            skipped.append(
                SkippedStore(
                    source_path,
                    RECORD_COLLISION,
                    f"record[{index}] duplicate id {expected_id} in store file",
                )
            )
            continue
        seen_ids.add(expected_id)

        # Strip claimed id — register() recomputes; status re-gated.
        payload = dict(item)
        payload.pop("id", None)
        # N014/N015: store integrity is the independent evidence carrier across
        # process restart. Re-admit verification_ids from a trusted store into
        # the proof ledger before authorize runs (live invent-via-register still
        # demotes without issue_verification_proof).
        vid = payload.get("verification_id")
        if isinstance(vid, str) and vid.strip():
            from windows_os_api.apps.api_registry.model import issue_verification_proof

            issue_verification_proof(vid.strip())
        try:
            registry.register(payload)
        except (RegistrationRejected, ValueError, TypeError, KeyError) as exc:
            skipped.append(
                SkippedStore(
                    source_path,
                    RECORD_REJECTED,
                    f"record[{index}] rejected: {exc}",
                )
            )


def load_registry(
    *,
    verification_max_age_sec: float | None = None,
) -> LoadReport:
    """Load store into a **new** ``ApiRegistry`` (process-restart simulation).

    Missing primary → empty OK. Corrupt primary → try bak. Both bad → empty
    + skips (never publish executable APIs from garbage).
    """
    kwargs: dict[str, Any] = {}
    if verification_max_age_sec is not None:
        kwargs["verification_max_age_sec"] = verification_max_age_sec
    registry = ApiRegistry(**kwargs)
    skipped: list[SkippedStore] = []

    primary = store_path()
    bak = bak_path()

    if not primary.exists() and not bak.exists():
        return LoadReport(
            registry=registry, skipped=[], recovered_from_bak=False, source_path=None
        )

    # Try primary first; on failure try bak.
    order: list[tuple[Path, bool]] = []
    if primary.exists():
        order.append((primary, False))
    if bak.exists():
        order.append((bak, True))

    primary_failed = False
    for path, is_bak in order:
        if is_bak and not primary_failed and primary.exists():
            # Only use bak when primary failed or was absent.
            continue
        raw, problem = _parse_store_file(path)
        if problem is not None:
            skipped.append(problem)
            if not is_bak:
                primary_failed = True
            continue
        assert raw is not None
        _ingest_records(
            registry,
            raw.get("records") or [],
            source_path=str(path),
            skipped=skipped,
        )
        if is_bak:
            skipped.append(
                SkippedStore(
                    str(path),
                    STORE_RECOVERED_FROM_BAK,
                    "primary unusable; loaded from backup",
                )
            )
        return LoadReport(
            registry=registry,
            skipped=skipped,
            recovered_from_bak=is_bak,
            source_path=str(path),
        )

    # Both unusable — empty registry, do not publish garbage.
    return LoadReport(
        registry=registry,
        skipped=skipped,
        recovered_from_bak=False,
        source_path=None,
    )


class PersistentApiRegistry(ApiRegistry):
    """``ApiRegistry`` that saves after successful mutations."""

    @classmethod
    def from_loaded(cls, loaded: ApiRegistry) -> "PersistentApiRegistry":
        """Promote an in-memory load result without re-saving during ingest.

        ``load_registry`` must keep using plain ``ApiRegistry`` so ingest does
        not rewrite the store on every record; the runtime singleton then wraps
        the loaded map in this class so mutations persist (audit H63-N015).
        """
        preg = cls(verification_max_age_sec=loaded._verification_max_age_sec)
        with preg._lock:
            preg._by_id = dict(loaded._by_id)
        return preg

    def register(self, payload, *, now=None, force_id=None):  # type: ignore[override]
        record = super().register(payload, now=now, force_id=force_id)
        save_registry(self)
        return record

    def set_status(self, api_id, status):  # type: ignore[override]
        record = super().set_status(api_id, status)
        save_registry(self)
        return record

    def clear(self) -> None:
        super().clear()
        save_registry(self)


def clear_registry_store() -> None:
    """Remove store files under ``store_dir()`` (test teardown)."""
    directory = store_dir()
    if not directory.exists():
        return
    for path in directory.iterdir():
        if path.is_file():
            path.unlink()
