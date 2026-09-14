"""N018 / H63-N018 — Verified result + API Test (postcondition + verification_id).

Unit/API matrix only (Q07/Q08). Full installed W/L H63-N018 is MANUAL (#21).
Coverage refs: R25 R32 W066-W067 L066-L067 S61-07 S61-20-S61-21 G12 G29.
"""
from __future__ import annotations

import pytest

from windows_os_api.apps.adapters.engine import (
    create_adapter,
    generate_adapter_openapi,
    get_adapter,
    reset_adapters,
    verify_and_record,
)
from windows_os_api.apps.adapters import store as adapter_store
from windows_os_api.apps.adapters import verification as verif
from windows_os_api.apps.api_registry import reset_api_registry, run_api_test
from windows_os_api.apps.api_registry.api_test import V_FAIL, V_PARTIAL, V_PASS, V_UNSUPPORTED
from windows_os_api.apps.api_registry.model import ApiStatus
from windows_os_api.apps.sandbox.permissions import reset_policies
from windows_os_api.backends.factory import reset_backend
from windows_os_api.core.runtime.config import get_settings

APP = "contoso-crm"


def _base(**overrides):
    payload = {
        "name": "Set customer name",
        "method": "POST",
        "path": f"/v1/apps/{APP}/actions/set_field_customer_name",
        "description": "Set customer name",
        "source": "virtual_adapter",
        "application_id": APP,
        "adapter_id": "adapter_demo",
        "capability": f"{APP}.set_field_customer_name",
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
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(tmp_path / "adapters"))
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


def _edit_name(adapter):
    return next(a.name for a in adapter.actions if a.control_type == "Edit")


def _button_name(adapter):
    return next(a.name for a in adapter.actions if a.control_type == "Button")


# ---------------------------------------------------------------------------
# verification_id minted only on VERIFIED; rollback still blocks
# ---------------------------------------------------------------------------


def test_verified_verdict_mints_verification_id(adapters):
    adapter = create_adapter(APP, hwnd=1001)
    name = _edit_name(adapter)
    verdict = verif.verify_action(APP, name)
    assert verdict["state"] == verif.VERIFIED, verdict
    assert isinstance(verdict.get("verification_id"), str)
    assert verdict["verification_id"].startswith("ver_")


def test_failed_verdict_has_no_verification_id(adapters, monkeypatch):
    adapter = create_adapter(APP, hwnd=1001)
    from windows_os_api.apps.adapters import engine

    real = engine.invoke_action

    def fail_probe(app_id, action_name, params=None):
        return {"ok": False, "error": "injected"}

    monkeypatch.setattr(engine, "invoke_action", fail_probe)
    # Need rollback path — use allow then fail style from security tests
    monkeypatch.setattr(engine, "invoke_action", real)
    calls = {"n": 0}

    def fail_then_rollback(app_id, action_name, params=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": False, "error": "injected probe fail"}
        return real(app_id, action_name, params)

    monkeypatch.setattr(engine, "invoke_action", fail_then_rollback)
    verdict = verif.verify_action(APP, _edit_name(adapter))
    assert verdict["state"] == verif.FAILED
    assert "verification_id" not in verdict


def test_failed_rollback_blocks_verified_and_id(adapters, monkeypatch):
    adapter = create_adapter(APP, hwnd=1001)
    from windows_os_api.apps.adapters import engine

    real = engine.invoke_action
    calls = {"n": 0}

    def probe_ok_rollback_fail(app_id, action_name, params=None):
        calls["n"] += 1
        if calls["n"] == 2:
            return {"ok": False, "error": "rollback fail"}
        return real(app_id, action_name, params)

    monkeypatch.setattr(engine, "invoke_action", probe_ok_rollback_fail)
    verdict = verif.verify_action(APP, _edit_name(adapter))
    assert verdict["state"] == verif.FAILED
    assert verdict["code"] == "ROLLBACK_FAILED"
    assert "verification_id" not in verdict


def test_verify_and_record_surfaces_verification_id(adapters):
    create_adapter(APP, hwnd=1001)
    name = _edit_name(get_adapter(APP))
    out = verify_and_record(APP, name, times=1)
    assert out["ok"] is True
    assert out["verification_id"]
    assert out["verification"]["verification_id"] == out["verification_id"]


def test_forged_verified_without_id_demoted_on_load(adapters, tmp_path):
    adapter = create_adapter(APP, hwnd=1001)
    action = next(a for a in adapter.actions if a.control_type == "Edit")
    action.verification = {
        "state": "VERIFIED",
        "evidence": "forged",
        "checked_at": 1.0,
        # no verification_id
    }
    adapter_store.save(adapter)
    reset_adapters()
    from windows_os_api.apps.adapters.engine import load_persisted_adapters

    load_persisted_adapters()
    loaded = get_adapter(APP)
    assert loaded is not None
    loaded_action = next(a for a in loaded.actions if a.name == action.name)
    assert loaded_action.verification is not None
    assert loaded_action.verification["state"] != "VERIFIED"
    assert loaded_action.verification.get("code") == "VERIFICATION_ID_MISSING"
    # Virtual API must not publish forged VERIFIED
    assert generate_adapter_openapi(loaded)["paths"] == {}


def test_openapi_requires_verification_id(adapters):
    adapter = create_adapter(APP, hwnd=1001)
    action = next(a for a in adapter.actions if a.control_type == "Edit")
    action.verification = {"state": "VERIFIED", "checked_at": 1.0}  # no id
    assert generate_adapter_openapi(adapter)["paths"] == {}
    action.verification = {
        "state": "VERIFIED",
        "checked_at": 1.0,
        "verification_id": "ver_abc",
    }
    paths = generate_adapter_openapi(adapter)["paths"]
    assert f"/v1/apps/{APP}/actions/{action.name}" in paths


# ---------------------------------------------------------------------------
# API Test: success only with independent postcondition + verification_id
# ---------------------------------------------------------------------------


def test_api_test_pass_mints_verification_id_and_success(registry, adapters):
    create_adapter(APP, hwnd=1001)
    name = _edit_name(get_adapter(APP))
    rec = registry.register(
        _base(
            path=f"/v1/apps/{APP}/actions/{name}",
            capability=f"{APP}.{name}",
            name=name,
        )
    )
    out = run_api_test(rec.id, params={"value": "n018-pass-value"}, registry=registry)
    assert out["success"] is True, out
    assert out["ok"] is True
    assert out["verification"]["status"] == V_PASS
    assert out["verification"]["verified"] is True
    assert out["verification_id"] and out["verification_id"].startswith("ver_")
    assert out["execution_id"].startswith("exec_")
    assert out["http_ok_alone"] is False
    updated = registry.get(rec.id)
    assert updated is not None
    assert updated.status is ApiStatus.VERIFIED
    assert updated.verification_id == out["verification_id"]


def test_api_test_http_ok_but_unchanged_state_is_not_success(registry, adapters, monkeypatch):
    """H63-N018: app accepts command but state unchanged → not success."""
    create_adapter(APP, hwnd=1001)
    name = _edit_name(get_adapter(APP))
    rec = registry.register(
        _base(
            path=f"/v1/apps/{APP}/actions/{name}",
            capability=f"{APP}.{name}",
            name=name,
        )
    )

    def fake_ok_no_effect(app_id, action_name, params=None):
        # Pretend invoke succeeded without writing
        return {"ok": True, "action": action_name, "set_value": (params or {}).get("value"), "noop": True}

    out = run_api_test(
        rec.id,
        params={"value": "should-not-appear"},
        registry=registry,
        invoke=fake_ok_no_effect,
        update_registry_on_pass=False,
    )
    assert out["execution"]["ok"] is True, out
    assert out["success"] is False, out
    assert out["verification"]["verified"] is False
    assert out["verification"]["status"] in {V_FAIL, V_PARTIAL}
    assert out["verification_id"] is None
    assert out["http_ok_alone"] is False


def test_api_test_button_is_partial_or_unsupported_not_verified(registry, adapters):
    create_adapter(APP, hwnd=1001)
    name = _button_name(get_adapter(APP))
    rec = registry.register(
        _base(
            path=f"/v1/apps/{APP}/actions/{name}",
            capability=f"{APP}.{name}",
            name=name,
            status="PARTIAL",
        )
    )
    out = run_api_test(rec.id, params={}, registry=registry, update_registry_on_pass=False)
    assert out["success"] is False
    assert out["verification"]["verified"] is False
    assert out["verification"]["status"] in {V_UNSUPPORTED, V_PARTIAL}
    assert out["verification_id"] is None


def test_api_test_missing_expected_value_is_partial(registry, adapters):
    create_adapter(APP, hwnd=1001)
    name = _edit_name(get_adapter(APP))
    rec = registry.register(
        _base(
            path=f"/v1/apps/{APP}/actions/{name}",
            capability=f"{APP}.{name}",
            name=name,
        )
    )
    out = run_api_test(rec.id, params={}, registry=registry, update_registry_on_pass=False)
    assert out["success"] is False
    assert out["verification"]["status"] == V_PARTIAL
    assert out["verification"]["verified"] is False
    assert out["verification_id"] is None


def test_api_test_unknown_api(registry, adapters):
    out = run_api_test("api_does_not_exist", registry=registry)
    assert out["success"] is False
    assert out["verification"]["status"] == V_FAIL
    assert out["verification_id"] is None


# ---------------------------------------------------------------------------
# HTTP surface: 200 with success=false is intentional
# ---------------------------------------------------------------------------


def test_rest_api_test_returns_200_with_success_false_when_unverified(client, auth_headers, registry, adapters):
    create_adapter(APP, hwnd=1001)
    name = _edit_name(get_adapter(APP))
    rec = registry.register(
        _base(
            path=f"/v1/apps/{APP}/actions/{name}",
            capability=f"{APP}.{name}",
            name=name,
        )
    )

    # Missing params.value → PARTIAL / unverified, but HTTP 200
    resp = client.post(
        f"/v1/apis/{rec.id}/test",
        headers=auth_headers,
        json={"params": {}, "update_registry_on_pass": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is False
    assert body["verification"]["verified"] is False
    assert body.get("verification_id") in (None, "")


def test_rest_api_test_pass_roundtrip(client, auth_headers, registry, adapters):
    create_adapter(APP, hwnd=1001)
    name = _edit_name(get_adapter(APP))
    rec = registry.register(
        _base(
            path=f"/v1/apps/{APP}/actions/{name}",
            capability=f"{APP}.{name}",
            name=name,
        )
    )
    resp = client.post(
        f"/v1/apis/{rec.id}/test",
        headers=auth_headers,
        json={"params": {"value": "via-http-n018"}, "update_registry_on_pass": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True, body
    assert body["verification_id"]
    assert body["verification"]["status"] == V_PASS
