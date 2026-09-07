"""MCP JSON-RPC handler hard tests."""
import os
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

def test_unknown_method():
    resp = handle_request({"jsonrpc": "2.0", "id": 9, "method": "nope"})
    assert resp["error"]["code"] == -32601
