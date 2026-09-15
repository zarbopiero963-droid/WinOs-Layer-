"""N042 — Correlated, redacted, anti-tamper JSONL audit logger.

Every entry carries unique ``request_id`` / ``execution_id``, secret redaction
before persist and before API return, HMAC hash-chain integrity, rotation /
retention, and fail-closed reads on corruption or unavailable storage.

Integrity secret: ``WINOS_AUDIT_INTEGRITY_SECRET`` (Settings.audit_integrity_secret).
Documented test/dev default: ``winos-audit-dev-secret`` — override in production.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from windows_os_api.core.events.schema import redact_secrets

# Documented default for tests / local — production must set WINOS_AUDIT_INTEGRITY_SECRET.
DEFAULT_INTEGRITY_SECRET = "winos-audit-dev-secret"
REDACTED = "[REDACTED]"

# API-key-like / bearer-like substrings in free-form string values.
_API_KEY_VALUE_RE = re.compile(
    r"(?i)\b("
    r"sk-[A-Za-z0-9_\-]{8,}"
    r"|Bearer\s+[A-Za-z0-9\-._~+/]+=*"
    r")\b"
)

# Control chars that would split or forge JSONL records.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


class AuditError(Exception):
    """Base audit failure (integrity / unavailable / bad input)."""


class AuditIntegrityError(AuditError):
    """JSONL chain truncated, altered, or injected."""


class AuditUnavailableError(AuditError):
    """Log path unwritable or parent missing after failure."""


class AuditParamError(AuditError):
    """Invalid pagination / input parameters."""


@dataclass(frozen=True)
class IntegrityReport:
    ok: bool
    entries_checked: int = 0
    reason: str = ""
    available: bool = True


def _new_id() -> str:
    return str(uuid.uuid4())


def sanitize_audit_string(value: str, *, max_len: int = 1024) -> str:
    """Strip/escape control chars so fields cannot split JSONL lines."""
    if not isinstance(value, str):
        value = str(value)
    cleaned = _CONTROL_RE.sub("", value.replace("\r", "").replace("\n", " "))
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned


def redact_audit_value(value: Any) -> Any:
    """Recursive secret-key drop + API-key-like string scrubbing."""
    redacted = redact_secrets(value)
    return _scrub_secret_strings(redacted)


def _scrub_secret_strings(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _scrub_secret_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_secret_strings(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub_secret_strings(v) for v in value)
    if isinstance(value, str):
        # Whole-string looks like a secret key name was already dropped;
        # scrub embedded token-like values.
        if _looks_like_secret_string(value):
            return REDACTED
        return _API_KEY_VALUE_RE.sub(REDACTED, value)
    return value


def _looks_like_secret_string(value: str) -> bool:
    lower = value.strip().lower()
    if lower.startswith("sk-") and len(value) >= 12:
        return True
    if lower.startswith("bearer ") and len(value) > 20:
        return True
    return False


def _canonical_for_hmac(entry: Mapping[str, Any]) -> bytes:
    """Stable bytes over entry fields excluding the ``hmac`` field itself."""
    payload = {k: entry[k] for k in sorted(entry.keys()) if k != "hmac"}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def compute_entry_hmac(entry: Mapping[str, Any], secret: bytes) -> str:
    return hmac.new(secret, _canonical_for_hmac(entry), hashlib.sha256).hexdigest()


class AuditLogger:
    def __init__(
        self,
        path: str | Path,
        *,
        integrity_secret: str | bytes | None = None,
        max_bytes: int = 10_485_760,
        max_entries: int = 100_000,
        max_age_seconds: float | None = None,
        create_parent: bool = True,
    ) -> None:
        self.path = Path(path)
        self.max_bytes = max(1024, int(max_bytes))
        self.max_entries = max(1, int(max_entries))
        self.max_age_seconds = max_age_seconds
        secret_raw = integrity_secret if integrity_secret is not None else DEFAULT_INTEGRITY_SECRET
        if isinstance(secret_raw, bytes):
            self._secret = secret_raw
        else:
            self._secret = str(secret_raw).encode("utf-8")
        self._lock = threading.Lock()
        self._prev_hash = "0" * 64
        self._entry_count = 0
        self._unavailable = False
        self._unavailable_reason = ""
        if create_parent:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._mark_unavailable(f"parent_mkdir_failed:{type(exc).__name__}")
        self._bootstrap_chain()

    def _mark_unavailable(self, reason: str) -> None:
        self._unavailable = True
        self._unavailable_reason = sanitize_audit_string(reason, max_len=200)
        try:
            from windows_os_api.observability.metrics import get_metrics

            get_metrics().incr("audit.unavailable")
        except Exception:
            pass

    def _bootstrap_chain(self) -> None:
        """Load last HMAC as prev_hash if file exists and verifies; else genesis."""
        if self._unavailable or not self.path.exists():
            return
        try:
            report = self.verify_integrity()
            if not report.ok:
                # Keep file for forensics; new writes continue from last readable hmac
                # only if we can parse trailing valid prefix — else genesis after rotate.
                self._prev_hash = self._last_hmac_best_effort() or ("0" * 64)
                self._entry_count = report.entries_checked
                return
            self._prev_hash = self._last_hmac_best_effort() or ("0" * 64)
            self._entry_count = report.entries_checked
        except OSError as exc:
            self._mark_unavailable(f"bootstrap_read_failed:{type(exc).__name__}")

    def _last_hmac_best_effort(self) -> str | None:
        try:
            with self.path.open("r", encoding="utf-8") as f:
                last = None
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s)
                        last = obj.get("hmac")
                    except json.JSONDecodeError:
                        continue
                return last if isinstance(last, str) else None
        except OSError:
            return None

    def log(
        self,
        action: str,
        *,
        subject: str = "system",
        resource: str = "",
        outcome: str = "success",
        detail: dict[str, Any] | None = None,
        request_id: str | None = None,
        execution_id: str | None = None,
    ) -> dict[str, Any]:
        if self._unavailable:
            try:
                from windows_os_api.observability.metrics import get_metrics

                get_metrics().incr("audit.write_failures")
            except Exception:
                pass
            raise AuditUnavailableError(
                f"audit_unavailable:{self._unavailable_reason or 'path_unwritable'}"
            )

        # Redact / sanitize outside the lock (CPU); chain fields under lock.
        rid = sanitize_audit_string(request_id or _new_id(), max_len=64)
        eid = sanitize_audit_string(execution_id or _new_id(), max_len=64)
        clean_detail = redact_audit_value(detail or {})
        if not isinstance(clean_detail, dict):
            clean_detail = {"value": clean_detail}
        action_s = sanitize_audit_string(action, max_len=256)
        subject_s = sanitize_audit_string(subject, max_len=256)
        resource_s = sanitize_audit_string(resource, max_len=512)
        outcome_s = sanitize_audit_string(outcome, max_len=64)

        with self._lock:
            if self._unavailable:
                raise AuditUnavailableError(
                    f"audit_unavailable:{self._unavailable_reason or 'path_unwritable'}"
                )
            try:
                self._rotate_if_needed_unlocked()
                entry: dict[str, Any] = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "action": action_s,
                    "subject": subject_s,
                    "resource": resource_s,
                    "outcome": outcome_s,
                    "detail": clean_detail,
                    "request_id": rid,
                    "execution_id": eid,
                    "prev_hash": self._prev_hash,
                }
                entry["hmac"] = compute_entry_hmac(entry, self._secret)
                line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
                # Defense: JSON must be single-line.
                if "\n" in line or "\r" in line:
                    line = line.replace("\r", "").replace("\n", " ")
                # Atomic-ish append: write full line then flush; tighten mode.
                existed = self.path.exists()
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                if not existed:
                    try:
                        os.chmod(self.path, 0o600)
                    except OSError:
                        pass
            except OSError as exc:
                self._mark_unavailable(f"write_failed:{type(exc).__name__}")
                try:
                    from windows_os_api.observability.metrics import get_metrics

                    get_metrics().incr("audit.write_failures")
                except Exception:
                    pass
                raise AuditUnavailableError(
                    f"audit_write_failed:{type(exc).__name__}"
                ) from exc

            self._prev_hash = entry["hmac"]
            self._entry_count += 1

        try:
            from windows_os_api.observability.metrics import get_metrics

            get_metrics().incr("audit.writes")
        except Exception:
            pass
        return entry

    def _rotate_if_needed_unlocked(self) -> None:
        if not self.path.exists():
            return
        size = self.path.stat().st_size
        need = size >= self.max_bytes or self._entry_count >= self.max_entries
        if self.max_age_seconds is not None and self.path.exists():
            age = datetime.now(timezone.utc).timestamp() - self.path.stat().st_mtime
            if age >= float(self.max_age_seconds):
                need = True
        if not need:
            return
        rotated = self.path.with_suffix(self.path.suffix + ".1")
        # Atomic replace: if .1 exists, unlink then rename
        if rotated.exists():
            rotated.unlink()
        os.replace(self.path, rotated)
        self._prev_hash = "0" * 64
        self._entry_count = 0

    def verify_integrity(self) -> IntegrityReport:
        if self._unavailable:
            return IntegrityReport(
                ok=False,
                available=False,
                reason=f"unavailable:{self._unavailable_reason or 'path'}",
            )
        if not self.path.exists():
            return IntegrityReport(ok=True, entries_checked=0, reason="empty")
        prev = "0" * 64
        checked = 0
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for lineno, raw in enumerate(f, start=1):
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        return IntegrityReport(
                            ok=False,
                            entries_checked=checked,
                            reason=f"invalid_json:line_{lineno}",
                        )
                    if not isinstance(obj, dict):
                        return IntegrityReport(
                            ok=False,
                            entries_checked=checked,
                            reason=f"non_object:line_{lineno}",
                        )
                    got = obj.get("hmac")
                    if not isinstance(got, str) or len(got) != 64:
                        return IntegrityReport(
                            ok=False,
                            entries_checked=checked,
                            reason=f"missing_hmac:line_{lineno}",
                        )
                    if obj.get("prev_hash") != prev:
                        return IntegrityReport(
                            ok=False,
                            entries_checked=checked,
                            reason=f"chain_break:line_{lineno}",
                        )
                    expected = compute_entry_hmac(obj, self._secret)
                    if not hmac.compare_digest(expected, got):
                        return IntegrityReport(
                            ok=False,
                            entries_checked=checked,
                            reason=f"hmac_mismatch:line_{lineno}",
                        )
                    prev = got
                    checked += 1
        except OSError as exc:
            return IntegrityReport(
                ok=False,
                available=False,
                reason=f"read_failed:{type(exc).__name__}",
            )
        return IntegrityReport(ok=True, entries_checked=checked, reason="ok")

    def _load_entries_verified(self) -> list[dict[str, Any]]:
        report = self.verify_integrity()
        if not report.available:
            raise AuditUnavailableError(report.reason or "audit_unavailable")
        if not report.ok:
            try:
                from windows_os_api.observability.metrics import get_metrics

                get_metrics().incr("audit.integrity_failures")
            except Exception:
                pass
            raise AuditIntegrityError(report.reason or "integrity_failed")
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                obj = json.loads(s)
                # Redact again on read (defense in depth for pre-N042 lines).
                if isinstance(obj.get("detail"), dict):
                    obj["detail"] = redact_audit_value(obj["detail"])
                entries.append(obj)
        return entries

    def read_all(self, *, verify: bool = True) -> list[dict[str, Any]]:
        """Return all entries. Default verify=True → fail-closed on corruption."""
        if verify:
            return self._load_entries_verified()
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj.get("detail"), dict):
                    obj["detail"] = redact_audit_value(obj["detail"])
                entries.append(obj)
        return entries

    def read_page(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        max_limit: int = 500,
    ) -> dict[str, Any]:
        """Secure pagination: clamp limit, reject negative/oversized, stable order."""
        if offset < 0:
            raise AuditParamError("offset_negative")
        if limit < 0:
            raise AuditParamError("limit_negative")
        if limit > max_limit:
            raise AuditParamError("limit_too_large")
        if offset > 1_000_000:
            raise AuditParamError("offset_too_large")
        clamped = min(max(limit, 0), max_limit)
        entries = self._load_entries_verified()
        # Stable chronological order (file order); page from start+offset.
        page = entries[offset : offset + clamped]
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for e in page:
            eid = e.get("execution_id") or e.get("request_id") or ""
            if eid and eid in seen:
                continue
            if eid:
                seen.add(str(eid))
            deduped.append(e)
        return {
            "entries": deduped,
            "limit": clamped,
            "offset": offset,
            "total": len(entries),
            "integrity": "ok",
        }


_audit: AuditLogger | None = None


def get_audit_logger(path: str | None = None) -> AuditLogger:
    global _audit
    if _audit is None:
        from windows_os_api.core.runtime.config import get_settings

        s = get_settings()
        p = path or s.audit_log_path
        _audit = AuditLogger(
            p,
            integrity_secret=s.audit_integrity_secret,
            max_bytes=s.audit_max_bytes,
            max_entries=s.audit_max_entries,
            max_age_seconds=s.audit_max_age_seconds,
        )
    return _audit


def reset_audit_logger() -> None:
    global _audit
    _audit = None
