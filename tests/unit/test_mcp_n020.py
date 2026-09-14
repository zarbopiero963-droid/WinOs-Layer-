"""N020 — Dynamic MCP tools from registry + runtime revoke (H63-N020)."""
from __future__ import annotations

import json
import time

import pytest

from windows_os_api.api.mcp.server import handle_request, list_all_tools
from windows_os_api.apps.adapters.engine import (
    create_adapter,
    get_adapter,
    reset_adapters,
    verify_and_record,
)
from windows_os_api.apps.api_registry.mcp_tools import (
    stable_mcp_tool_name,
)
from windows_os_api.apps.api_registry.model import (
    ApiStatus,
    reset_api_registry,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_adapters()
    reset_api_registry()
    yield
    reset_adapters()
    reset_api_registry()


def _register_verified(**overrides):
    reg = reset_api_registry()
    # keep same registry singleton after reset
    payload = {
        "name": "Probe Search",
        "method": "POST",
        "path": "/v1/apps/contoso-crm/actions/search_probe",
        "description": "Probe search capability",
        "source": "virtual_adapter",
        "application_id": "contoso-crm",
        "capability": "contoso-crm.search_probe",
        "status": "VERIFIED",
        "verification_id": "ver_mcp_n020",
        "last_verified_at": time.time(),
        "permissions": ["ui.read", "ui.control"],
        "authentication_required": True,
    }
    payload.update(overrides)
    return reg.register(payload), reg


def test_protocol_negotiation_accepts_supported_and_rejects_unknown():
    ok = handle_request({
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    })
    assert "error" not in ok
    assert ok["result"]["protocolVersion"] == "2024-11-05"
    assert ok["result"]["capabilities"]["tools"].get("listChanged") is True

    bad = handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "1999-01-01"},
    })
    assert bad["error"]["code"] == -32602
    assert "Unsupported" in bad["error"]["message"]


def test_tools_list_includes_verified_registry_tool_with_stable_name():
    rec, _ = _register_verified()
    resp = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in resp["result"]["tools"]]
    expected = stable_mcp_tool_name(rec)
    assert expected in names
    tool = next(t for t in resp["result"]["tools"] if t["name"] == expected)
    assert tool["annotations"]["x-api-id"] == rec.id
    assert tool["annotations"]["x-verification-state"] == "VERIFIED"
    # twin list stable
    resp2 = handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert [t["name"] for t in resp["result"]["tools"]] == [
        t["name"] for t in resp2["result"]["tools"]
    ]


def test_unverified_registry_api_absent_from_tools_list():
    reg = reset_api_registry()
    reg.register({
        "name": "Partial",
        "method": "POST",
        "path": "/v1/apps/x/actions/y",
        "source": "virtual_adapter",
        "application_id": "x",
        "capability": "x.y",
        "status": "PARTIAL",
        "permissions": [],
        "description": "partial",
    })
    names = [t["name"] for t in list_all_tools()]
    assert not any(n.startswith("registry_") for n in names)


def test_runtime_revoke_removes_tool_and_blocks_call(tmp_sandbox):
    # Real verified Edit action + registry row pointing at it
    adapter = create_adapter("contoso-crm", hwnd=1001)
    edit = next(a for a in adapter.actions if a.control_type == "Edit")
    verified = verify_and_record("contoso-crm", edit.name)
    assert verified["verification"]["state"] == "VERIFIED"

    rec, reg = _register_verified(
        path=f"/v1/apps/contoso-crm/actions/{edit.name}",
        capability=f"contoso-crm.{edit.name}",
        name=edit.name,
        verification_id=verified["verification"]["verification_id"],
    )
    tool_name = stable_mcp_tool_name(rec)
    listed = handle_request({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    assert tool_name in [t["name"] for t in listed["result"]["tools"]]

    # Execute while VERIFIED — gateway path (adapter present)
    call = handle_request({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": {"params": {"value": "n020-effect"}},
        },
    })
    assert "error" not in call, call
    body = json.loads(call["result"]["content"][0]["text"])
    assert body.get("ok") is True, body

    # Revoke
    reg.set_status(rec.id, ApiStatus.DISABLED)
    listed2 = handle_request({"jsonrpc": "2.0", "id": 6, "method": "tools/list"})
    assert tool_name not in [t["name"] for t in listed2["result"]["tools"]]

    denied = handle_request({
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": {"params": {"value": "should-block"}},
        },
    })
    assert denied["error"]["code"] == -32001, denied
    assert "revoked" in denied["error"]["message"].lower() or "not verified" in denied["error"]["message"].lower() or "not available" in denied["error"]["message"].lower()


def test_invoke_action_does_not_implicitly_create_adapter():
    reset_adapters()
    assert get_adapter("contoso-crm") is None
    resp = handle_request({
        "jsonrpc": "2.0", "id": 8, "method": "tools/call",
        "params": {
            "name": "invoke_action",
            "arguments": {"app_id": "contoso-crm", "action": "nope", "params": {}},
        },
    })
    # Gateway operational failure → content with ok=False, OR error
    assert get_adapter("contoso-crm") is None, "must not create adapter"
    if "error" in resp:
        return
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body.get("ok") is False
    assert body.get("created_adapter") is False
