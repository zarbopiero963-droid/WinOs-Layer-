"""MCP-style JSON-RPC server (stdio + in-process handler).

N020: ``tools/list`` merges stable platform tools with VERIFIED registry tools;
runtime revoke (DISABLED / demoted) removes tools and fails ``tools/call``;
``invoke_action`` never implicitly ``create_adapter`` (N017); protocol
negotiation accepts known versions only.

N011: ``auth_context_from_mcp_params`` builds the shared AuthContext from
``params._meta`` (same subject/fingerprint as REST/WS).

N021: ``resources/list`` / ``resources/read`` expose VERIFIED registry state
(``winos://api/{id}``) without secrets; ``resources.listChanged`` capability +
pending ``notifications/resources/list_changed`` (and tools twin) on snapshot
drift; optional ``params._meta.app_scopes`` for cross-user parity with REST
catalog; ``handle_message`` maps malformed JSON to ``-32700``.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from windows_os_api.apps.adapters.engine import (
    create_adapter,
    get_adapter,
    list_adapters,
    verify_and_record,
)
from windows_os_api.apps.api_registry.gateway import execute_via_gateway
from windows_os_api.apps.api_registry.mcp_resources import (
    list_registry_mcp_resources,
    read_registry_mcp_resource,
    resource_snapshot_uris,
)
from windows_os_api.apps.api_registry.mcp_tools import (
    REGISTRY_TOOL_PREFIX,
    call_registry_mcp_tool,
    list_registry_mcp_tools,
)
from windows_os_api.apps.discovery import service as discovery
from windows_os_api.apps.agent.computer import ComputerAgent
from windows_os_api.backends.factory import get_backend
from windows_os_api.core.security.auth import AuthContext, auth_context_from_mcp_params
from windows_os_api.os.system import service as system

SUPPORTED_PROTOCOL_VERSIONS = frozenset({"2024-11-05", "2025-03-26"})
DEFAULT_PROTOCOL_VERSION = "2024-11-05"

# Platform baseline tools (not invented from unverified registry rows).
PLATFORM_TOOLS: list[dict[str, Any]] = [
    {"name": "system_info", "description": "Get OS system info", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_apps", "description": "Discover installed apps", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_adapters", "description": "List virtual adapters", "inputSchema": {"type": "object", "properties": {}}},
    {
        "name": "create_adapter",
        "description": "Create adapter for an app (explicit only)",
        "inputSchema": {
            "type": "object",
            "properties": {"app_id": {"type": "string"}, "hwnd": {"type": "integer"}},
            "required": ["app_id"],
        },
    },
    {
        "name": "invoke_action",
        "description": "Invoke adapter action via shared gateway (no implicit create)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "app_id": {"type": "string"},
                "action": {"type": "string"},
                "params": {"type": "object"},
            },
            "required": ["app_id", "action"],
        },
    },
    {
        "name": "verify_action",
        "description": "Verify an adapter action by observing and rolling back its effect",
        "inputSchema": {
            "type": "object",
            "properties": {
                "app_id": {"type": "string"},
                "action": {"type": "string"},
                "times": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["app_id", "action"],
        },
    },
    {
        "name": "agent_run",
        "description": "Run computer agent toward a goal",
        "inputSchema": {
            "type": "object",
            "properties": {"goal": {"type": "string"}, "app_id": {"type": "string"}},
            "required": ["goal", "app_id"],
        },
    },
    {"name": "ui_tree", "description": "Get UI automation tree", "inputSchema": {
        "type": "object", "properties": {"hwnd": {"type": "integer"}}
    }},
]

# Back-compat alias for tests that import TOOLS (platform baseline only).
TOOLS = PLATFORM_TOOLS

# --- N021 notification / snapshot state (process-local MCP session) ---
_pending_notifications: list[dict[str, Any]] = []
_last_resource_snapshot: tuple[str, ...] | None = None
_last_tool_snapshot: tuple[str, ...] | None = None

_last_tool_snapshot: tuple[str, ...] | None = None

# N011 — shared principal for the in-flight MCP request (None = unauthenticated).
_current_mcp_auth: AuthContext | None = None


def current_mcp_auth() -> AuthContext | None:
    """AuthContext built for the current MCP request, if any (N011)."""
    return _current_mcp_auth


def _bind_mcp_auth(params: Any) -> AuthContext | None:
    """Construct shared AuthContext from ``params._meta`` (no parallel identity)."""
    global _current_mcp_auth
    p = params if isinstance(params, dict) else {}
    # Only build when a key is offered, or when require_auth is off (anonymous VIEWER).
    # Missing credentials under require_auth stay None — deny/list gating is N020.
    meta = p.get("_meta") if isinstance(p.get("_meta"), dict) else {}
    has_key = isinstance(meta, dict) and (
        meta.get("api_key") is not None or meta.get("x-api-key") is not None
    )
    from windows_os_api.core.runtime.config import get_settings
    settings = get_settings()
    if has_key or not settings.require_auth:
        _current_mcp_auth = auth_context_from_mcp_params(p, settings)
    else:
        _current_mcp_auth = None
    return _current_mcp_auth



def take_pending_notifications() -> list[dict[str, Any]]:
    """Drain server→client notifications queued since the last take (N021)."""
    global _pending_notifications
    out = list(_pending_notifications)
    _pending_notifications = []
    return out


def reset_mcp_session_state() -> None:
    """Clear notification queue and listChanged snapshots (tests / restart)."""
    global _pending_notifications, _last_resource_snapshot, _last_tool_snapshot
    global _current_mcp_auth
    _pending_notifications = []
    _last_resource_snapshot = None
    _last_tool_snapshot = None
    _current_mcp_auth = None


def _enqueue_notification(method: str, params: dict[str, Any] | None = None) -> None:
    _pending_notifications.append({
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
    })


def _visible_app_ids_from_params(params: Any) -> frozenset[str] | None:
    """Optional MCP ``params._meta.app_scopes`` → catalog-style visibility.

    ``None`` = unrestricted (default, matches ADMIN / unbound REST principals).
    A list/tuple (even empty) = scoped allowlist.
    """
    if not isinstance(params, dict):
        return None
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None
    if "app_scopes" not in meta:
        return None
    scopes = meta.get("app_scopes")
    if scopes is None:
        return None
    if isinstance(scopes, str):
        scopes = [scopes]
    if not isinstance(scopes, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(s).strip() for s in scopes if str(s).strip())


def _tool_snapshot() -> tuple[str, ...]:
    return tuple(t["name"] for t in list_all_tools())


def _maybe_emit_list_changed(visible: frozenset[str] | None) -> None:
    """Compare snapshots and enqueue list_changed notifications (N021)."""
    global _last_resource_snapshot, _last_tool_snapshot
    res_now = resource_snapshot_uris(visible_app_ids=visible)
    tools_now = _tool_snapshot()
    if _last_resource_snapshot is not None and res_now != _last_resource_snapshot:
        _enqueue_notification("notifications/resources/list_changed")
    if _last_tool_snapshot is not None and tools_now != _last_tool_snapshot:
        _enqueue_notification("notifications/tools/list_changed")
    _last_resource_snapshot = res_now
    _last_tool_snapshot = tools_now


def list_all_tools() -> list[dict[str, Any]]:
    """Platform tools + VERIFIED registry tools (stable order)."""
    dyn = list_registry_mcp_tools()
    # Drop registry tools whose names collide with platform names (fail-closed skip).
    platform_names = {t["name"] for t in PLATFORM_TOOLS}
    dyn = [t for t in dyn if t["name"] not in platform_names]
    return list(PLATFORM_TOOLS) + dyn


def handle_message(raw: str | bytes) -> dict[str, Any]:
    """Parse a raw JSON-RPC line and dispatch (N021 malformed → -32700)."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        req = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error"},
        }
    if not isinstance(req, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
    return handle_request(req)


def handle_request(req: dict[str, Any]) -> dict[str, Any]:
    rid = req.get("id")
    method = req.get("method", "")
    params = req.get("params") or {}
    try:
        _bind_mcp_auth(params)
    except Exception as exc:  # noqa: BLE001 — map FastAPI HTTPException to JSON-RPC
        from fastapi import HTTPException
        if isinstance(exc, HTTPException):
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32001, "message": str(exc.detail)},
            }
        raise

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def err(code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

    if method == "initialize":
        client_ver = ""
        if isinstance(params, dict):
            client_ver = str(params.get("protocolVersion") or params.get("protocol_version") or "").strip()
        if client_ver and client_ver not in SUPPORTED_PROTOCOL_VERSIONS:
            return err(
                -32602,
                f"Unsupported MCP protocolVersion {client_ver!r}; "
                f"supported={sorted(SUPPORTED_PROTOCOL_VERSIONS)}",
            )
        negotiated = client_ver or DEFAULT_PROTOCOL_VERSION
        return ok({
            "protocolVersion": negotiated,
            "capabilities": {
                "tools": {"listChanged": True},
                "resources": {"listChanged": True, "subscribe": False},
            },
            "serverInfo": {"name": "winos-mcp", "version": "1.0.0"},
        })
    if method == "tools/list":
        visible = _visible_app_ids_from_params(params)
        _maybe_emit_list_changed(visible)
        return ok({"tools": list_all_tools()})
    if method == "tools/call":
        name = params.get("name") if isinstance(params, dict) else None
        arguments = (params.get("arguments") or {}) if isinstance(params, dict) else {}
        try:
            result = call_tool(name, arguments)
            # Security denials from gateway / revoke → JSON-RPC error (not fake success).
            if isinstance(result, dict) and result.get("ok") is False and result.get("denied"):
                return err(-32001, result.get("error") or result.get("message") or "denied")
            return ok({"content": [{"type": "text", "text": json.dumps(result)}]})
        except Exception as e:  # noqa: BLE001
            return err(-32000, str(e))
    if method == "resources/list":
        visible = _visible_app_ids_from_params(params)
        _maybe_emit_list_changed(visible)
        return ok({"resources": list_registry_mcp_resources(visible_app_ids=visible)})
    if method == "resources/read":
        if not isinstance(params, dict):
            return err(-32602, "resources/read: params must be an object")
        uri = params.get("uri")
        if not uri:
            return err(-32602, "resources/read: uri is required")
        visible = _visible_app_ids_from_params(params)
        _maybe_emit_list_changed(visible)
        outcome = read_registry_mcp_resource(str(uri), visible_app_ids=visible)
        if outcome.get("ok") is False and outcome.get("denied"):
            return err(-32001, outcome.get("error") or "resource denied")
        return ok({"contents": outcome.get("contents") or []})
    if method == "ping":
        return ok({"ok": True})
    return err(-32601, f"Method not found: {method}")


def _require_declared_arguments(name: str, arguments: dict[str, Any]) -> None:
    """Enforce each tool's own `required` list before dispatching."""
    schema = None
    for t in list_all_tools():
        if t["name"] == name:
            schema = t.get("inputSchema")
            break
    if not schema:
        return
    missing = [k for k in schema.get("required", []) if k not in arguments]
    if missing:
        raise ValueError(
            f"{name}: missing required argument(s): {', '.join(missing)}"
        )


def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    if not name:
        raise ValueError("tools/call: name is required")
    # Registry-backed dynamic tools — re-check publishability at call time.
    if name.startswith(REGISTRY_TOOL_PREFIX):
        return call_registry_mcp_tool(name, arguments)

    _require_declared_arguments(name, arguments)
    if name == "system_info":
        return system.system_info()
    if name == "list_apps":
        return discovery.discover()
    if name == "list_adapters":
        return list_adapters()
    if name == "create_adapter":
        a = create_adapter(arguments["app_id"], hwnd=int(arguments.get("hwnd", 1001)))
        return {"app_id": a.app_id, "actions": [x.name for x in a.actions]}
    if name == "invoke_action":
        # N017/N020: never create adapters implicitly — shared gateway only.
        from windows_os_api.core.runtime.config import get_settings

        outcome = execute_via_gateway(
            app_id=arguments["app_id"],
            action_name=arguments["action"],
            params=arguments.get("params") or {},
            auth=current_mcp_auth(),
            require_principal=bool(get_settings().require_auth),
        )
        return outcome
    if name == "verify_action":
        times = int(arguments.get("times", 1))
        if times < 1 or times > 10:
            raise ValueError("verify_action: times must be between 1 and 10")
        return verify_and_record(arguments["app_id"], arguments["action"], times=times)
    if name == "agent_run":
        return ComputerAgent(arguments["app_id"]).run(arguments["goal"])
    if name == "ui_tree":
        return get_backend().get_ui_tree(arguments.get("hwnd"))
    raise ValueError(f"Unknown tool: {name}")


def main() -> None:
    """Stdio JSON-RPC loop."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        resp = handle_message(line)
        # Flush any pending notifications before the response (N021 events).
        for note in take_pending_notifications():
            sys.stdout.write(json.dumps(note) + "\n")
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
