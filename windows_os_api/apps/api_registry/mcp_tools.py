"""N020 — Dynamic MCP tools from the API Registry (H63-N020).

``tools/list`` exposes VERIFIED + non-empty ``verification_id`` records with
stable names/schemas. DISABLED / revoked / non-VERIFIED entries are omitted.
``tools/call`` re-checks the registry at invoke time (runtime revoke) and
routes through ``execute_via_gateway`` — same execution layer as REST.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from windows_os_api.apps.api_registry.gateway import execute_via_gateway


def _mcp_auth():
    """Lazy import to avoid mcp_tools ↔ mcp.server circular import."""
    from windows_os_api.api.mcp.server import current_mcp_auth

    return current_mcp_auth()
from windows_os_api.apps.api_registry.model import (
    ApiRecord,
    ApiRegistry,
    ApiStatus,
    get_api_registry,
)

_SAFE = re.compile(r"[^a-zA-Z0-9_]+")

# Tool name prefix for registry-backed tools (stable, unique).
REGISTRY_TOOL_PREFIX = "registry_"


def stable_mcp_tool_name(record: ApiRecord | Mapping[str, Any]) -> str:
    """Deterministic MCP tool name from api id (+ capability fragment)."""
    if isinstance(record, ApiRecord):
        api_id = record.id
        capability = record.capability
    else:
        api_id = str(record.get("id") or "")
        capability = str(record.get("capability") or "")
    suffix = api_id.removeprefix("api_")[:20] or "unknown"
    cap = _SAFE.sub("_", (capability or "op").strip()).strip("_")[:40] or "op"
    return f"{REGISTRY_TOOL_PREFIX}{cap}_{suffix}"


def _is_mcp_publishable(record: ApiRecord) -> bool:
    if record.status is not ApiStatus.VERIFIED:
        return False
    vid = record.verification_id
    return isinstance(vid, str) and bool(vid.strip())


def record_to_mcp_tool(record: ApiRecord) -> dict[str, Any]:
    """OpenAPI-adjacent JSON Schema tool descriptor for MCP tools/list."""
    name = stable_mcp_tool_name(record)
    return {
        "name": name,
        "description": record.description or record.name,
        "inputSchema": {
            "type": "object",
            "properties": {
                "params": {
                    "type": "object",
                    "description": f"Parameters for {record.capability}",
                    "additionalProperties": True,
                }
            },
            "additionalProperties": False,
        },
        "annotations": {
            "x-api-id": record.id,
            "x-capability": record.capability,
            "x-verification-state": ApiStatus.VERIFIED.value,
            "x-verification-id": record.verification_id,
            "x-method": record.method,
            "x-path": record.path,
            "x-permissions": list(record.permissions),
        },
    }


def list_registry_mcp_tools(
    registry: ApiRegistry | None = None,
) -> list[dict[str, Any]]:
    """VERIFIED registry tools only, sorted by stable name."""
    reg = registry if registry is not None else get_api_registry()
    tools = [
        record_to_mcp_tool(r)
        for r in reg.list()
        if _is_mcp_publishable(r)
    ]
    tools.sort(key=lambda t: t["name"])
    return tools


def find_record_for_tool_name(
    tool_name: str,
    registry: ApiRegistry | None = None,
) -> ApiRecord | None:
    """Resolve a registry_* tool name → current ApiRecord (or None)."""
    if not tool_name.startswith(REGISTRY_TOOL_PREFIX):
        return None
    reg = registry if registry is not None else get_api_registry()
    for rec in reg.list():
        if stable_mcp_tool_name(rec) == tool_name:
            return rec
    return None


def call_registry_mcp_tool(
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    registry: ApiRegistry | None = None,
) -> dict[str, Any]:
    """Execute a registry MCP tool via the shared gateway (runtime revoke).

    Re-loads the record by tool name; if missing / not VERIFIED / DISABLED,
    returns a security deny envelope (does not invent success).
    """
    args = dict(arguments or {})
    rec = find_record_for_tool_name(tool_name, registry=registry)
    if rec is None:
        return {
            "ok": False,
            "denied": True,
            "block_kind": "security",
            "code": "MCP_TOOL_REVOKED_OR_UNKNOWN",
            "error": f"MCP tool not available (revoked or unknown): {tool_name}",
        }
    if not _is_mcp_publishable(rec):
        return {
            "ok": False,
            "denied": True,
            "block_kind": "security",
            "code": "MCP_TOOL_NOT_VERIFIED",
            "error": (
                f"MCP tool {tool_name} is not VERIFIED "
                f"(status={rec.status.value})"
            ),
            "api_id": rec.id,
            "effective_status": rec.status.value,
        }

    # Prefer path-derived app/action for virtual adapter routes.
    app_id = rec.application_id
    action_name = ""
    path = rec.path or ""
    marker = "/actions/"
    if marker in path:
        action_name = path.rsplit(marker, 1)[-1].strip("/")
    if not app_id and path.startswith("/v1/apps/"):
        parts = path.split("/")
        # /v1/apps/{app}/actions/{action}
        if len(parts) >= 4:
            app_id = parts[3]

    params = args.get("params") if isinstance(args.get("params"), dict) else args
    outcome = execute_via_gateway(
        api_id=rec.id,
        app_id=app_id or None,
        action_name=action_name or None,
        params=params if isinstance(params, dict) else {},
        registry=registry,
        auth=_mcp_auth(),
    )
    return outcome
