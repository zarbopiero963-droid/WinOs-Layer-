"""MCP JSON-RPC handler hard tests."""
import os
import json
os.environ["WINOS_BACKEND"] = "fake"
from windows_os_api.core.runtime.config import get_settings
from windows_os_api.backends.factory import reset_backend
from windows_os_api.apps.adapters.engine import reset_adapters
from windows_os_api.api.mcp.server import handle_request, TOOLS

def setup_function():
    get_settings.cache_clear()
    reset_backend()
    reset_adapters()

def test_tools_list():
    resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert resp["id"] == 1
    names = [t["name"] for t in resp["result"]["tools"]]
    assert "system_info" in names
    assert "invoke_action" in names
    assert "verify_action" in names
    assert len(TOOLS) >= 5

def test_initialize_and_call():
    init = handle_request({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "winos-mcp"
    call = handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "system_info", "arguments": {}},
    })
    assert "result" in call
    assert "content" in call["result"]

def test_create_adapter_tool():
    resp = handle_request({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "create_adapter", "arguments": {"app_id": "contoso-crm"}},
    })
    assert "error" not in resp
    text = resp["result"]["content"][0]["text"]
    assert "contoso-crm" in text


def test_verify_action_tool_observes_and_rolls_back():
    created = handle_request({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "create_adapter", "arguments": {"app_id": "contoso-crm"}},
    })
    assert "error" not in created, created
    from windows_os_api.apps.adapters.engine import get_adapter

    adapter = get_adapter("contoso-crm")
    action = next(a for a in adapter.actions if a.control_type == "Edit")
    response = handle_request({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {
            "name": "verify_action",
            "arguments": {"app_id": "contoso-crm", "action": action.name, "times": 2},
        },
    })
    assert "error" not in response, response
    result = json.loads(response["result"]["content"][0]["text"])
    assert result["ok"] is True, result
    assert result["verification"]["state"] == "VERIFIED", result
    assert result["verification"]["attempts"] == 2, result


def test_verify_action_tool_rejects_unbounded_repetitions():
    response = handle_request({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {
            "name": "verify_action",
            "arguments": {"app_id": "contoso-crm", "action": "any", "times": 11},
        },
    })
    assert response["error"]["code"] == -32000, response
    assert "between 1 and 10" in response["error"]["message"], response

def test_unknown_method():
    resp = handle_request({"jsonrpc": "2.0", "id": 9, "method": "nope"})
    assert resp["error"]["code"] == -32601
