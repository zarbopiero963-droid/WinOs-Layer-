"""N021 — MCP resources, events/notifications, REST/MCP parity (H63-N021)."""
from __future__ import annotations

import json
import time

import pytest

from windows_os_api.api.mcp.server import (
    handle_message,
    handle_request,
    reset_mcp_session_state,
    take_pending_notifications,
)
from windows_os_api.apps.adapters.engine import (
    create_adapter,
    reset_adapters,
    verify_and_record,
)
from windows_os_api.apps.api_registry.gateway import execute_via_gateway
from windows_os_api.apps.api_registry.mcp_tools import stable_mcp_tool_name
from windows_os_api.apps.api_registry.model import (
    issue_verification_proof,
    ApiStatus,
    reset_api_registry,
)
from windows_os_api.core.runtime.config import get_settings
from windows_os_api.core.security.auth import get_auth_registry, reset_auth_registry



def _mcp_params(extra: dict | None = None, **meta_extra) -> dict:
    """Authenticated MCP params (N020 require_auth)."""
    meta = {"api_key": "admin-key-change-me", **meta_extra}
    params = dict(extra or {})
    existing = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    params["_meta"] = {**meta, **existing}
    return params

@pytest.fixture(autouse=True)
def _clean():
    reset_adapters()
    reset_api_registry()
    reset_auth_registry()
    get_settings.cache_clear()
    reset_mcp_session_state()
    yield
    reset_adapters()
    reset_api_registry()
    reset_auth_registry()
    get_settings.cache_clear()
    reset_mcp_session_state()


def _register_verified(**overrides):
    reg = reset_api_registry()
    payload = {
        "name": "Probe Search",
        "method": "POST",
        "path": "/v1/apps/contoso-crm/actions/search_probe",
        "description": "Probe search capability",
        "source": "virtual_adapter",
        "application_id": "contoso-crm",
        "capability": "contoso-crm.search_probe",
        "status": "VERIFIED",
        "verification_id": issue_verification_proof("ver_mcp_n021"),
        "last_verified_at": time.time(),
        "permissions": ["ui.read", "ui.control"],
        "authentication_required": True,
    }
    payload.update(overrides)
    # reset_api_registry() clears the proof ledger — re-issue the final vid.
    vid = payload.get("verification_id")
    if isinstance(vid, str) and vid.strip():
        payload["verification_id"] = issue_verification_proof(vid.strip())
    return reg.register(payload), reg


def test_initialize_advertises_resources_list_changed():
    ok = handle_request({
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    })
    assert "error" not in ok, ok
    caps = ok["result"]["capabilities"]
    assert caps.get("tools", {}).get("listChanged") is True
    assert caps.get("resources", {}).get("listChanged") is True


def test_resources_list_includes_verified_registry_uri():
    rec, _ = _register_verified()
    resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
    assert "error" not in resp, resp
    uris = [r["uri"] for r in resp["result"]["resources"]]
    expected = f"winos://api/{rec.id}"
    assert expected in uris
    entry = next(r for r in resp["result"]["resources"] if r["uri"] == expected)
    assert entry["name"] == rec.name or rec.capability in entry.get("name", "")
    assert "mimeType" in entry


def test_resources_read_returns_registry_state_without_secrets():
    rec, _ = _register_verified()
    # Inject secret-like fields into a parallel dict path via description abuse
    # is not enough — resource layer must strip known secret keys if present
    # on the serialized payload helpers.
    uri = f"winos://api/{rec.id}"
    resp = handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "resources/read",
        "params": {"uri": uri},
    })
    assert "error" not in resp, resp
    contents = resp["result"]["contents"]
    assert len(contents) == 1
    body = json.loads(contents[0]["text"])
    assert body["id"] == rec.id
    assert body["status"] == "VERIFIED"
    assert body["capability"] == rec.capability
    for banned in ("api_key", "password", "secret", "token", "credential", "private_key"):
        assert banned not in body


def test_resource_payload_redacts_injected_secrets():
    """Even if a record dict carries secret keys, MCP resource must redact them."""
    from windows_os_api.apps.api_registry import mcp_resources as mr

    dirty = {
        "id": "api_x",
        "capability": "x.y",
        "status": "VERIFIED",
        "api_key": "sk-live-secret",
        "password": "p@ss",
        "token": "tok",
        "nested": {"secret": "nope", "ok": 1},
    }
    clean = mr.redact_secrets(dirty)
    assert "api_key" not in clean
    assert "password" not in clean
    assert "token" not in clean
    assert "secret" not in clean.get("nested", {})
    assert clean["nested"]["ok"] == 1
    assert clean["id"] == "api_x"


def test_unverified_absent_from_resources_list():
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
    resp = handle_request({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    uris = [r["uri"] for r in resp["result"]["resources"]]
    assert not any("api_" in u and "/x" in u for u in uris) or all(
        "PARTIAL" not in json.dumps(resp)
        for _ in [0]
    )
    # No resource for non-VERIFIED
    assert all("/api_partial" not in u for u in uris)
    assert len([u for u in uris if u.startswith("winos://api/")]) == 0


def test_runtime_revoke_removes_resource_and_blocks_read():
    rec, reg = _register_verified()
    uri = f"winos://api/{rec.id}"
    listed = handle_request({"jsonrpc": "2.0", "id": 4, "method": "resources/list"})
    assert uri in [r["uri"] for r in listed["result"]["resources"]]

    reg.set_status(rec.id, ApiStatus.DISABLED)

    listed2 = handle_request({"jsonrpc": "2.0", "id": 5, "method": "resources/list"})
    assert uri not in [r["uri"] for r in listed2["result"]["resources"]]

    denied = handle_request({
        "jsonrpc": "2.0", "id": 6, "method": "resources/read",
        "params": {"uri": uri},
    })
    assert denied["error"]["code"] in (-32001, -32602, -32002), denied
    assert "revoked" in denied["error"]["message"].lower() or "not available" in denied["error"]["message"].lower() or "not found" in denied["error"]["message"].lower() or "disabled" in denied["error"]["message"].lower()


def test_list_changed_notification_after_revoke():
    reset_mcp_session_state()
    rec, reg = _register_verified()
    handle_request({"jsonrpc": "2.0", "id": 7, "method": "resources/list"})
    take_pending_notifications()  # clear snapshot baseline

    reg.set_status(rec.id, ApiStatus.DISABLED)
    # Next list/read path should enqueue notifications
    handle_request({"jsonrpc": "2.0", "id": 8, "method": "resources/list"})
    notes = take_pending_notifications()
    methods = [n.get("method") for n in notes]
    assert "notifications/resources/list_changed" in methods or "notifications/tools/list_changed" in methods


def test_malformed_json_returns_parse_error():
    resp = handle_message("{not-json")
    assert resp["error"]["code"] == -32700
    assert resp["id"] is None


def test_rest_mcp_same_capability_same_effect():
    """Same capability via gateway (REST path) and MCP tools/call → same effect."""
    adapter = create_adapter("contoso-crm", hwnd=1001)
    edit = next(a for a in adapter.actions if a.control_type == "Edit")
    verified = verify_and_record("contoso-crm", edit.name)
    assert verified["verification"]["state"] == "VERIFIED"

    rec, _ = _register_verified(
        path=f"/v1/apps/contoso-crm/actions/{edit.name}",
        capability=f"contoso-crm.{edit.name}",
        name=edit.name,
        verification_id=verified["verification"]["verification_id"],
    )
    params = {"value": "n021-parity"}

    rest_out = execute_via_gateway(
        api_id=rec.id,
        app_id="contoso-crm",
        action_name=edit.name,
        params=params,
    )
    tool_name = stable_mcp_tool_name(rec)
    mcp = handle_request({
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"_meta": {"api_key": "admin-key-change-me"}, "name": tool_name, "arguments": {"params": params}},
    })
    assert "error" not in mcp, mcp
    mcp_out = json.loads(mcp["result"]["content"][0]["text"])

    assert rest_out.get("ok") is True
    assert mcp_out.get("ok") is True
    # Shared identity / policy fields
    assert rest_out.get("denied") == mcp_out.get("denied")
    assert rest_out.get("block_kind") == mcp_out.get("block_kind")
    assert rest_out.get("api_id") == mcp_out.get("api_id") or rest_out.get("api_id") == rec.id


def test_rest_mcp_deny_parity_on_revoke():
    adapter = create_adapter("contoso-crm", hwnd=1001)
    edit = next(a for a in adapter.actions if a.control_type == "Edit")
    verified = verify_and_record("contoso-crm", edit.name)
    rec, reg = _register_verified(
        path=f"/v1/apps/contoso-crm/actions/{edit.name}",
        capability=f"contoso-crm.{edit.name}",
        name=edit.name,
        verification_id=verified["verification"]["verification_id"],
    )
    reg.set_status(rec.id, ApiStatus.DISABLED)

    rest_out = execute_via_gateway(api_id=rec.id, app_id="contoso-crm", action_name=edit.name, params={})
    tool_name = stable_mcp_tool_name(rec)
    mcp = handle_request({
        "jsonrpc": "2.0", "id": 10, "method": "tools/call",
        "params": {"_meta": {"api_key": "admin-key-change-me"}, "name": tool_name, "arguments": {"params": {}}},
    })
    assert rest_out.get("ok") is False
    assert rest_out.get("denied") is True
    assert "error" in mcp
    assert mcp["error"]["code"] == -32001


def test_cross_user_resource_hidden_by_app_scopes():
    rec, _ = _register_verified(application_id="secret-app", capability="secret-app.op")
    uri = f"winos://api/{rec.id}"

    # Scoped principal: only allowed-app
    resp = handle_request({
        "jsonrpc": "2.0", "id": 11, "method": "resources/list",
        "params": {"_meta": {"app_scopes": ["allowed-app"]}},
    })
    assert "error" not in resp, resp
    uris = [r["uri"] for r in resp["result"]["resources"]]
    assert uri not in uris

    denied = handle_request({
        "jsonrpc": "2.0", "id": 12, "method": "resources/read",
        "params": {"uri": uri, "_meta": {"app_scopes": ["allowed-app"]}},
    })
    assert "error" in denied

    # Unrestricted sees it
    ok = handle_request({
        "jsonrpc": "2.0", "id": 13, "method": "resources/list",
        "params": {},
    })
    assert uri in [r["uri"] for r in ok["result"]["resources"]]


def test_restart_clears_then_reregister_restores_resource():
    rec, _ = _register_verified()
    uri = f"winos://api/{rec.id}"
    assert uri in [
        r["uri"]
        for r in handle_request({"jsonrpc": "2.0", "id": 14, "method": "resources/list"})["result"]["resources"]
    ]
    # Simulate process restart of in-memory registry
    reset_api_registry()
    empty = handle_request({"jsonrpc": "2.0", "id": 15, "method": "resources/list"})
    assert uri not in [r["uri"] for r in empty["result"]["resources"]]
    rec2, _ = _register_verified()
    uri2 = f"winos://api/{rec2.id}"
    again = handle_request({"jsonrpc": "2.0", "id": 16, "method": "resources/list"})
    assert uri2 in [r["uri"] for r in again["result"]["resources"]]
