"""MCP-style JSON-RPC server (stdio + in-process handler)."""
from __future__ import annotations

import json
import sys
from typing import Any

from windows_os_api.apps.adapters.engine import create_adapter, get_adapter, invoke_action, list_adapters
from windows_os_api.apps.discovery import service as discovery
from windows_os_api.apps.agent.computer import ComputerAgent
from windows_os_api.backends.factory import get_backend
from windows_os_api.os.system import service as system


TOOLS = [
    {"name": "system_info", "description": "Get OS system info", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_apps", "description": "Discover installed apps", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_adapters", "description": "List virtual adapters", "inputSchema": {"type": "object", "properties": {}}},
    {
        "name": "create_adapter",
        "description": "Create adapter for an app",
        "inputSchema": {
            "type": "object",
            "properties": {"app_id": {"type": "string"}, "hwnd": {"type": "integer"}},
            "required": ["app_id"],
        },
    },
    {
        "name": "invoke_action",
        "description": "Invoke adapter action",
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
        "name": "agent_run",
        "description": "Run computer agent toward a goal",
        "inputSchema": {
            "type": "object",
            "properties": {"goal": {"type": "string"}, "app_id": {"type": "string"}},
            "required": ["goal"],
        },
    },
    {"name": "ui_tree", "description": "Get UI automation tree", "inputSchema": {
        "type": "object", "properties": {"hwnd": {"type": "integer"}}
    }},
]


def handle_request(req: dict[str, Any]) -> dict[str, Any]:
    rid = req.get("id")
    method = req.get("method", "")
    params = req.get("params") or {}

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def err(code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "winos-mcp", "version": "1.0.0"},
        })
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            result = call_tool(name, arguments)
            return ok({"content": [{"type": "text", "text": json.dumps(result)}]})
        except Exception as e:  # noqa: BLE001
            return err(-32000, str(e))
    if method == "ping":
        return ok({"ok": True})
    return err(-32601, f"Method not found: {method}")


def call_tool(name: str, arguments: dict[str, Any]) -> Any:
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
        if not get_adapter(arguments["app_id"]):
            create_adapter(arguments["app_id"])
        return invoke_action(arguments["app_id"], arguments["action"], arguments.get("params"))
    if name == "agent_run":
        return ComputerAgent(arguments.get("app_id", "contoso-crm")).run(arguments["goal"])
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
