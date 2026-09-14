"""N017 / H63-N017 — Shared execution gateway (no implicit create).

Unit/API matrix only (Q02/Q08). Full installed W/L H63-N017 is MANUAL (#21).
Coverage refs: R32 R35 R36 R47 W065 L065 S61-06 S61-12 G08 G13 G20 G29.
"""
from __future__ import annotations

import time

import pytest

from windows_os_api.apps.adapters.engine import (
    create_adapter,
    get_adapter,
    invoke_action,
    reset_adapters,
)
from windows_os_api.apps.api_registry import (
    AuthorizationDecision,
    ExecutionCode,
    authorize_execution,
    execute_via_gateway,
    gateway_http_status,
    reset_api_registry,
)
from windows_os_api.apps.api_registry.model import (
    DEFAULT_VERIFICATION_MAX_AGE_SEC,
    ApiStatus,
)
from windows_os_api.apps.sandbox.permissions import SandboxPolicy, reset_policies, set_policy
from windows_os_api.backends.factory import reset_backend
from windows_os_api.core.runtime.config import get_settings


APP = "contoso-crm"


def _base(**overrides):
    payload = {
        "name": "Set email",
        "method": "POST",
        "path": f"/v1/apps/{APP}/actions/set_field_email",
        "description": "Set email",
        "source": "virtual_adapter",
        "application_id": APP,
        "adapter_id": "adapter_demo",
        "capability": f"{APP}.set_field_email",
        "permissions": ["ui.read", "ui.control"],
        "authentication_required": True,
        "status": "PARTIAL",
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def registry():
    reg = reset_api_registry()
    yield reg
    reset_api_registry()


@pytest.fixture()
def adapters(tmp_path, monkeypatch):
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(tmp_path))
    monkeypatch.setenv("WINOS_BACKEND", "fake")
    get_settings.cache_clear()
    reset_backend()
    reset_adapters()
    reset_policies()
    yield
    reset_adapters()
    reset_policies()
    get_settings.cache_clear()
    reset_backend()


# ---------------------------------------------------------------------------
# Absent adapter — no implicit create (critical fail-open kill)
# ---------------------------------------------------------------------------


def test_absent_adapter_denied_operational_no_create(registry, adapters):
    assert get_adapter(APP) is None
    decision = authorize_execution(
        app_id=APP, action_name="set_field_email", registry=registry
    )
    assert decision.allowed is False
    assert decision.block_kind == "operational"
    assert decision.code == ExecutionCode.ADAPTER_ABSENT.value
    assert get_adapter(APP) is None  # still absent — no create

    out = execute_via_gateway(
        app_id=APP, action_name="set_field_email", registry=registry
    )
    assert out["ok"] is False
    assert out["denied"] is False
    assert out["block_kind"] == "operational"
    assert out["code"] == ExecutionCode.ADAPTER_ABSENT.value
    assert out["created_adapter"] is False
    assert get_adapter(APP) is None
    assert gateway_http_status(out) == 404


def test_execute_never_calls_create_adapter(registry, adapters, monkeypatch):
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("create_adapter must not be called from gateway")

    import windows_os_api.apps.adapters.engine as engine

    monkeypatch.setattr(engine, "create_adapter", boom)
    out = execute_via_gateway(
        app_id=APP, action_name="set_field_email", registry=registry
    )
    assert out["ok"] is False
    assert calls["n"] == 0
    assert get_adapter(APP) is None


# ---------------------------------------------------------------------------
# DISABLED / revoked / ERROR / RESTRICTED / UNSUPPORTED
# ---------------------------------------------------------------------------


def test_disabled_api_security_block_no_engine_call(registry, adapters, monkeypatch):
    create_adapter(APP, hwnd=1001)
    registry.register(_base(status="DISABLED"))
    called = {"n": 0}

    def spy(*a, **k):
        called["n"] += 1
        return {"ok": True}

    out = execute_via_gateway(
        app_id=APP,
        action_name="set_field_email",
        registry=registry,
        invoke=spy,
    )
    assert out["ok"] is False
    assert out["denied"] is True
    assert out["block_kind"] == "security"
    assert out["code"] == ExecutionCode.API_DISABLED.value
    assert called["n"] == 0
    assert gateway_http_status(out) == 403


def test_revoked_lifecycle_maps_disabled_and_blocks(registry, adapters):
    create_adapter(APP, hwnd=1001)
    # REVOKED → DISABLED at register
    rec = registry.register(_base(status=None, lifecycle="REVOKED"))
    assert rec.status is ApiStatus.DISABLED
    decision = authorize_execution(
        app_id=APP, action_name="set_field_email", registry=registry
    )
    assert decision.allowed is False
    assert decision.block_kind == "security"
    assert decision.code == ExecutionCode.API_DISABLED.value


def test_error_status_blocks_execution(registry, adapters):
    create_adapter(APP, hwnd=1001)
    registry.register(_base(status="ERROR"))
    decision = authorize_execution(
        app_id=APP, action_name="set_field_email", registry=registry
    )
    assert decision.allowed is False
    assert decision.code == ExecutionCode.API_ERROR_STATUS.value
    assert decision.block_kind == "security"


def test_restricted_and_unsupported_block(registry, adapters):
    create_adapter(APP, hwnd=1001)
    registry.register(
        _base(
            path=f"/v1/apps/{APP}/actions/click_btn_save",
            capability=f"{APP}.click_btn_save",
            status="RESTRICTED",
            name="Save",
        )
    )
    d1 = authorize_execution(
        app_id=APP, action_name="click_btn_save", registry=registry
    )
    assert d1.code == ExecutionCode.API_RESTRICTED.value

    registry.register(
        _base(
            path=f"/v1/apps/{APP}/actions/click_btn_cancel",
            capability=f"{APP}.click_btn_cancel",
            status="UNSUPPORTED",
            name="Cancel",
        )
    )
    d2 = authorize_execution(
        app_id=APP, action_name="click_btn_cancel", registry=registry
    )
    assert d2.code == ExecutionCode.API_UNSUPPORTED.value


# ---------------------------------------------------------------------------
# Stale VERIFIED
# ---------------------------------------------------------------------------


def test_stale_verified_refuses_execute(registry, adapters):
    create_adapter(APP, hwnd=1001)
    stale_ts = time.time() - (DEFAULT_VERIFICATION_MAX_AGE_SEC + 3600)
    rec = registry.register(
        _base(
            status="VERIFIED",
            verification_id="ver_old",
            last_verified_at=stale_ts,
        )
    )
    # register() demotes to PARTIAL — still must not execute as verified;
    # for execute path we also refuse when caller forces a stale clock check
    # on a record that somehow still claims VERIFIED.
    assert rec.status is ApiStatus.PARTIAL

    # Force a VERIFIED-looking record via set_status after planting evidence,
    # then age it out by re-checking with `now` far in the future through
    # authorize_execution's stale gate on an explicitly VERIFIED row.
    # Re-register with fresh evidence then authorize with a future `now`.
    fresh = time.time()
    rec2 = registry.register(
        _base(
            status="VERIFIED",
            verification_id="ver_fresh",
            last_verified_at=fresh,
            path=f"/v1/apps/{APP}/actions/set_field_phone",
            capability=f"{APP}.set_field_phone",
            name="Set phone",
        )
    )
    assert rec2.status is ApiStatus.VERIFIED
    decision = authorize_execution(
        app_id=APP,
        action_name="set_field_phone",
        registry=registry,
        now=fresh + DEFAULT_VERIFICATION_MAX_AGE_SEC + 10,
    )
    assert decision.allowed is False
    assert decision.code == ExecutionCode.API_STALE.value
    assert decision.block_kind == "security"


# ---------------------------------------------------------------------------
# Missing API by id vs legacy path without registry row
# ---------------------------------------------------------------------------


def test_missing_api_id_is_operational(registry, adapters):
    decision = authorize_execution(api_id="api_does_not_exist", registry=registry)
    assert decision.allowed is False
    assert decision.block_kind == "operational"
    assert decision.code == ExecutionCode.API_NOT_FOUND.value


def test_legacy_invoke_without_registry_row_allows_when_adapter_present(
    registry, adapters
):
    create_adapter(APP, hwnd=1001)
    decision = authorize_execution(
        app_id=APP, action_name="set_field_email", registry=registry
    )
    assert decision.allowed is True
    assert decision.record is None
    out = execute_via_gateway(
        app_id=APP,
        action_name="set_field_email",
        params={"value": "a@b.it"},
        registry=registry,
    )
    assert out["ok"] is True
    assert out["created_adapter"] is False
    assert out.get("set_value") == "a@b.it" or out["result"]["set_value"] == "a@b.it"


def test_partial_with_adapter_executes(registry, adapters):
    create_adapter(APP, hwnd=1001)
    registry.register(_base(status="PARTIAL"))
    out = execute_via_gateway(
        app_id=APP,
        action_name="set_field_email",
        params={"value": "x@y.it"},
        registry=registry,
    )
    assert out["ok"] is True
    assert out["code"] == ExecutionCode.OK.value


# ---------------------------------------------------------------------------
# Security block vs operational error (engine)
# ---------------------------------------------------------------------------


def test_security_block_distinct_from_engine_operational_error(registry, adapters):
    create_adapter(APP, hwnd=1001)
    registry.register(_base(status="DISABLED"))
    sec = execute_via_gateway(
        app_id=APP, action_name="set_field_email", registry=registry
    )
    assert sec["block_kind"] == "security"
    assert sec["denied"] is True

    # Clear registry block; force engine operational failure via missing action
    reset_api_registry()
    reg = reset_api_registry()
    create_adapter(APP, hwnd=1001)
    op = execute_via_gateway(
        app_id=APP,
        action_name="definitely_missing_action_xyz",
        registry=reg,
    )
    assert op["ok"] is False
    assert op["denied"] is False
    assert op["block_kind"] == "operational"
    assert op["code"] == ExecutionCode.ENGINE_ERROR.value


def test_gated_path_honours_sandbox_policy_as_security(registry, adapters):
    adapter = create_adapter(APP, hwnd=1001)
    action = next(a for a in adapter.actions if "save" in a.name and "btn" in a.name)
    set_policy(
        SandboxPolicy(app_id=APP, denied_actions={action.name}, max_risk="high")
    )
    # Direct engine also denies — gated path must surface as security, not bypass
    direct = invoke_action(APP, action.name, {})
    assert direct.get("denied") is True

    out = execute_via_gateway(
        app_id=APP, action_name=action.name, registry=registry
    )
    assert out["ok"] is False
    assert out["denied"] is True
    assert out["block_kind"] == "security"
    assert out["code"] == ExecutionCode.ENGINE_DENIED.value


def test_direct_engine_still_works_but_rest_path_uses_gateway(registry, adapters):
    """Direct engine does not consult registry; gateway does — no bypass via gate."""
    create_adapter(APP, hwnd=1001)
    registry.register(_base(status="DISABLED"))
    # Direct engine ignores registry (by design for N017 shared helper readiness)
    direct = invoke_action(APP, "set_field_email", {"value": "z@z.it"})
    assert direct.get("ok") is True
    # Gated path must refuse
    gated = execute_via_gateway(
        app_id=APP,
        action_name="set_field_email",
        params={"value": "z@z.it"},
        registry=registry,
    )
    assert gated["ok"] is False
    assert gated["code"] == ExecutionCode.API_DISABLED.value


# ---------------------------------------------------------------------------
# Lookup by api_id
# ---------------------------------------------------------------------------


def test_authorize_by_api_id_resolves_app_and_action(registry, adapters):
    create_adapter(APP, hwnd=1001)
    rec = registry.register(_base(status="PARTIAL"))
    decision = authorize_execution(api_id=rec.id, registry=registry)
    assert decision.allowed is True
    assert decision.app_id == APP
    assert decision.action_name == "set_field_email"
    out = execute_via_gateway(
        api_id=rec.id,
        params={"value": "id@path.it"},
        registry=registry,
        require_registry_record=True,
    )
    assert out["ok"] is True
    assert out["api_id"] == rec.id


def test_require_registry_record_without_match_fails(registry, adapters):
    create_adapter(APP, hwnd=1001)
    decision = authorize_execution(
        app_id=APP,
        action_name="set_field_email",
        registry=registry,
        require_registry_record=True,
    )
    assert decision.allowed is False
    assert decision.code == ExecutionCode.API_NOT_FOUND.value


# ---------------------------------------------------------------------------
# REST: no implicit create on invoke
# ---------------------------------------------------------------------------


def test_rest_invoke_absent_adapter_returns_404_no_create(client, auth_headers, adapters):
    assert get_adapter(APP) is None
    r = client.post(
        f"/v1/apps/{APP}/actions/set_field_email",
        headers=auth_headers,
        json={"params": {"value": "nope@x.it"}},
    )
    assert r.status_code == 404, r.text
    body = r.json()["detail"]
    assert body["code"] == ExecutionCode.ADAPTER_ABSENT.value
    assert body["block_kind"] == "operational"
    assert get_adapter(APP) is None


def test_rest_invoke_disabled_returns_403(client, auth_headers, adapters, registry):
    create_adapter(APP, hwnd=1001)
    # Ensure discovery knows the app for ensure_app_access paths
    client.post("/v1/apps/discover", headers=auth_headers)
    client.post(f"/v1/apps/{APP}/adapter", headers=auth_headers, json={"hwnd": 1001})
    registry.register(_base(status="DISABLED"))
    r = client.post(
        f"/v1/apps/{APP}/actions/set_field_email",
        headers=auth_headers,
        json={"params": {"value": "nope@x.it"}},
    )
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail["code"] == ExecutionCode.API_DISABLED.value
    assert detail["block_kind"] == "security"
    assert detail["denied"] is True


def test_rest_invoke_ok_when_adapter_created_explicitly(
    client, auth_headers, adapters, registry
):
    client.post("/v1/apps/discover", headers=auth_headers)
    created = client.post(
        f"/v1/apps/{APP}/adapter", headers=auth_headers, json={"hwnd": 1001}
    )
    assert created.status_code == 200, created.text
    registry.register(_base(status="PARTIAL"))
    r = client.post(
        f"/v1/apps/{APP}/actions/set_field_email",
        headers=auth_headers,
        json={"params": {"value": "ok@contoso.it"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_authorization_decision_exportable():
    """MCP/GUI can import the shared types (N011-style readiness)."""
    assert AuthorizationDecision is not None
    assert callable(authorize_execution)
    assert callable(execute_via_gateway)
