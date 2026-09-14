"""N021 — MCP resources from the API Registry (H63-N021).

``resources/list`` / ``resources/read`` expose VERIFIED registry records as
MCP resources (``winos://api/{api_id}``). Payloads are redacted (no secrets).
Visibility follows the same ``visible_app_ids`` rule as the REST catalog
(cross-user / app_scopes). Runtime revoke (DISABLED / non-VERIFIED) removes
the resource and fails ``resources/read``.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from windows_os_api.apps.api_registry.mcp_tools import _is_mcp_publishable
from windows_os_api.apps.api_registry.model import (
    ApiRecord,
    ApiRegistry,
    get_api_registry,
)


def _record_visible(record: ApiRecord, visible_app_ids: frozenset[str] | None) -> bool:
    """Same visibility rule as REST catalog (N016)."""
    if visible_app_ids is None:
        return True
    app = (record.application_id or "").strip()
    if not app:
        return False
    return app in visible_app_ids

RESOURCE_URI_PREFIX = "winos://api/"
RESOURCE_MIME = "application/json"

# Keys (case-insensitive) stripped from resource payloads — never leak secrets.
_SECRET_KEYS = frozenset({
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
})


def redact_secrets(value: Any) -> Any:
    """Recursively drop secret-named keys from dict payloads."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if str(k).strip().lower() in _SECRET_KEYS:
                continue
            out[str(k)] = redact_secrets(v)
        return out
    if isinstance(value, list):
        return [redact_secrets(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(v) for v in value)
    return value


def resource_uri_for(api_id: str) -> str:
    return f"{RESOURCE_URI_PREFIX}{api_id}"


def parse_resource_uri(uri: str) -> str | None:
    """Return api_id from ``winos://api/{id}`` or None if not our scheme."""
    if not isinstance(uri, str):
        return None
    u = uri.strip()
    if not u.startswith(RESOURCE_URI_PREFIX):
        return None
    api_id = u[len(RESOURCE_URI_PREFIX) :].strip().strip("/")
    return api_id or None


def record_to_mcp_resource(record: ApiRecord) -> dict[str, Any]:
    """MCP resources/list entry for a registry record."""
    return {
        "uri": resource_uri_for(record.id),
        "name": record.name or record.capability or record.id,
        "description": record.description or f"Registry API {record.capability}",
        "mimeType": RESOURCE_MIME,
        "annotations": {
            "x-api-id": record.id,
            "x-capability": record.capability,
            "x-verification-state": record.status.value,
            "x-application-id": record.application_id,
        },
    }


def list_registry_mcp_resources(
    *,
    registry: ApiRegistry | None = None,
    visible_app_ids: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """VERIFIED (+ verification_id) resources, scoped like REST catalog."""
    reg = registry if registry is not None else get_api_registry()
    resources = [
        record_to_mcp_resource(r)
        for r in reg.list()
        if _is_mcp_publishable(r) and _record_visible(r, visible_app_ids)
    ]
    resources.sort(key=lambda r: r["uri"])
    return resources


def read_registry_mcp_resource(
    uri: str,
    *,
    registry: ApiRegistry | None = None,
    visible_app_ids: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Read one resource. Returns MCP contents envelope or a deny dict.

    Deny dict shape: ``{"ok": False, "denied": True, "error": ..., "code": ...}``
    so the MCP server can map to JSON-RPC errors consistently with tools/call.
    """
    api_id = parse_resource_uri(uri)
    if not api_id:
        return {
            "ok": False,
            "denied": True,
            "code": "MCP_RESOURCE_URI_INVALID",
            "error": f"Unsupported or invalid resource URI: {uri!r}",
        }
    reg = registry if registry is not None else get_api_registry()
    rec = reg.get(api_id)
    if rec is None or not _record_visible(rec, visible_app_ids):
        return {
            "ok": False,
            "denied": True,
            "code": "MCP_RESOURCE_NOT_FOUND",
            "error": f"MCP resource not available (revoked, hidden, or unknown): {uri}",
        }
    if not _is_mcp_publishable(rec):
        return {
            "ok": False,
            "denied": True,
            "code": "MCP_RESOURCE_REVOKED",
            "error": (
                f"MCP resource not available (revoked or disabled): {uri} "
                f"(status={rec.status.value})"
            ),
            "api_id": rec.id,
            "effective_status": rec.status.value,
        }

    payload = redact_secrets(rec.to_dict())
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        "ok": True,
        "contents": [
            {
                "uri": resource_uri_for(rec.id),
                "mimeType": RESOURCE_MIME,
                "text": text,
            }
        ],
    }


def resource_snapshot_uris(
    *,
    registry: ApiRegistry | None = None,
    visible_app_ids: frozenset[str] | None = None,
) -> tuple[str, ...]:
    """Stable URI tuple for listChanged detection."""
    return tuple(
        r["uri"]
        for r in list_registry_mcp_resources(
            registry=registry, visible_app_ids=visible_app_ids
        )
    )
