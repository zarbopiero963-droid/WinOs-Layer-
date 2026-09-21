"""N038 — Event type allowlist, provenance, and secret redaction before fan-out.

Unknown types (e.g. ``admin.granted``) and oversized/deep payloads are rejected
*before* history or any subscriber sees them. Secret-named keys are stripped
recursively from accepted payloads so WebSocket / history cannot leak them.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

# Production event types only. Tests must use these (or extend this set in a
# follow-up PR) — free-form types are the B-BUS defect N038 closes.
ALLOWED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "agent.goal.proposed",
        "agent.goal.executed",
        "agent.goal.denied",
        "system.probe",
        "workflow.step.proposed",
        "workflow.step.executed",
        "api.registry.changed",
        "system.crash",
    }
)

ALLOWED_PROVENANCE: frozenset[str] = frozenset(
    {
        "internal",
        "agent",
        "api",
        "system",
        "websocket",
    }
)

# Keys (case-insensitive) stripped from payloads — aligned with MCP N021.
SECRET_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "password",
        "secret",
        "token",
        "credential",
        "credentials",
        "private_key",
        "privatekey",
        "access_token",
        "refresh_token",
        "authorization",
        "x_api_key",
        "x-api-key",
    }
)

MAX_PAYLOAD_BYTES = 8192
MAX_PAYLOAD_DEPTH = 8
MAX_PAYLOAD_KEYS = 64


def redact_secrets(value: Any) -> Any:
    """Recursively drop secret-named keys from dict payloads."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key_l = str(k).strip().lower()
            if key_l in SECRET_KEYS or key_l.replace("-", "_") in SECRET_KEYS:
                continue
            out[str(k)] = redact_secrets(v)
        return out
    if isinstance(value, list):
        return [redact_secrets(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(v) for v in value)
    return value


def _payload_depth(value: Any, depth: int = 0) -> int:
    if isinstance(value, Mapping):
        if not value:
            return depth
        return max(_payload_depth(v, depth + 1) for v in value.values())
    if isinstance(value, (list, tuple)):
        if not value:
            return depth
        return max(_payload_depth(v, depth + 1) for v in value)
    return depth


def _count_keys(value: Any) -> int:
    if isinstance(value, Mapping):
        n = len(value)
        for v in value.values():
            n += _count_keys(v)
        return n
    if isinstance(value, (list, tuple)):
        return sum(_count_keys(v) for v in value)
    return 0


def payload_byte_size(payload: Mapping[str, Any]) -> int:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    return len(raw.encode("utf-8"))


def validate_and_sanitize(
    event_type: str,
    payload: dict[str, Any] | None,
    *,
    provenance: str = "internal",
) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(clean_payload, None)`` if accepted, else ``(None, reason)``.

    Rejection reasons are stable strings for tests and diagnostics. Rejected
    events must not enter history or subscriber queues.
    """
    if not isinstance(event_type, str) or not event_type.strip():
        return None, "type_invalid"
    if event_type not in ALLOWED_EVENT_TYPES:
        return None, "type_not_allowed"
    if provenance not in ALLOWED_PROVENANCE:
        return None, "provenance_not_allowed"
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return None, "payload_not_object"
    if _payload_depth(payload) > MAX_PAYLOAD_DEPTH:
        return None, "payload_too_deep"
    if _count_keys(payload) > MAX_PAYLOAD_KEYS:
        return None, "payload_too_many_keys"
    clean = redact_secrets(payload)
    if not isinstance(clean, dict):
        return None, "payload_not_object"
    if payload_byte_size(clean) > MAX_PAYLOAD_BYTES:
        return None, "payload_too_large"
    return clean, None
