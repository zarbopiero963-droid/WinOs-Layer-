"""MCP-style JSON-RPC server (stdio + in-process handler).

N020: ``tools/list`` merges stable platform tools with VERIFIED registry tools;
runtime revoke (DISABLED / demoted) removes tools and fails ``tools/call``;
``invoke_action`` never implicitly ``create_adapter`` (N017); protocol
negotiation accepts known versions only.
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
from windows_os_api.apps.api_registry.mcp_tools import (
    REGISTRY_TOOL_PREFIX,
    call_registry_mcp_tool,
    list_registry_mcp_tools,
)
from windows_os_api.apps.discovery import service as discovery
from windows_os_api.apps.agent.computer import ComputerAgent
from windows_os_api.backends.factory import get_backend
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


def list_all_tools() -> list[dict[str, Any]]:
    """Platform tools + VERIFIED registry tools (stable order)."""
    dyn = list_registry_mcp_tools()
    # Drop registry tools whose names collide with platform names (fail-closed skip).
    platform_names = {t["name"] for t in PLATFORM_TOOLS}
    dyn = [t for t in dyn if t["name"] not in platform_names]
    return list(PLATFORM_TOOLS) + dyn


def handle_request(req: dict[str, Any]) -> dict[str, Any]:
    rid = req.get("id")
    method = req.get("method", "")
    params = req.get("params") or {}

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
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "winos-mcp", "version": "1.0.0"},
        })
    if method == "tools/list":
        return ok({"tools": list_all_tools()})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            result = call_tool(name, arguments)
            # Security denials from gateway / revoke → JSON-RPC error (not fake success).
            if isinstance(result, dict) and result.get("ok") is False and result.get("denied"):
                return err(-32001, result.get("error") or result.get("message") or "denied")
            return ok({"content": [{"type": "text", "text": json.dumps(result)}]})
        except Exception as e:  # noqa: BLE001
            return err(-32000, str(e))
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
        outcome = execute_via_gateway(
            app_id=arguments["app_id"],
            action_name=arguments["action"],
            params=arguments.get("params") or {},
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
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}) + "\n")
            sys.stdout.flush()
            continue
        resp = handle_request(req)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
