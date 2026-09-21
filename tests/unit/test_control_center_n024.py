"""N024 / H63-N024 — Control Center Create → Verify → Publish.

Unit/API matrix (Q08/Q13). Full installed W/L H63-N024 is MANUAL (#21).
Coverage refs: R22 R31 R40 W092 L092 S61-10–S61-11 G28 G29.
"""
from __future__ import annotations

import re
import time

import pytest

from windows_os_api.apps.api_registry import reset_api_registry
from windows_os_api.apps.api_registry.api_test import run_api_test
from windows_os_api.apps.api_registry.model import ApiStatus, issue_verification_proof


def _base(**overrides):
    payload = {
        "name": "Customer Search",
        "method": "POST",
        "path": "/v1/apps/example/customer/search",
        "description": "Search customer",
        "source": "virtual_adapter",
        "application_id": "example-app",
        "adapter_id": "adapter_demo",
        "capability": "customer.search",
        "permissions": ["ui.read"],
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


def _html(client) -> str:
    r = client.get("/")
    assert r.status_code == 200, r.text
    assert "text/html" in (r.headers.get("content-type") or "")
    return r.text


def test_control_center_n024_cvp_markers(client):
    html = _html(client)
    assert "Create → Verify → Publish" in html or "Create" in html
    assert "/v1/apis" in html
    assert "/publish" in html or "cvpPublish" in html
    assert "update_registry_on_pass" in html
    assert "cvpCreate" in html or 'id="btn_cvp_create"' in html
    assert "cvpVerify" in html or 'id="btn_cvp_verify"' in html
    assert "cvpPublish" in html or 'id="btn_cvp_publish"' in html
    assert "window.confirm" in html  # explicit consent for publish


def test_control_center_n024_api_key_not_prefilled(client):
    html = _html(client)
    assert re.search(r'id="key"[^>]*value=""', html) or 'id="key" value=""' in html
    assert 'value="dev"' not in html
    assert 'value="admin"' not in html
    assert "dev-key-change-me" not in html
    assert "admin-key-change-me" not in html


def test_control_center_n024_textcontent_anti_xss(client):
    html = _html(client)
    assert "textContent" in html
    assert "setCvpOut" in html or "cvp_out" in html
    assert not re.search(
        r"innerHTML\s*=\s*[^;]*(r\.body|cvp|result|rec\.name|rec\.description)",
        html,
    )


def test_rest_create_always_partial(client, admin_headers, registry):
    r = client.post(
        "/v1/apis",
        headers=admin_headers,
        json={
            "name": "N024 Create",
            "method": "POST",
            "path": "/v1/apps/example/n024-create",
            "description": "candidate",
            "source": "virtual_adapter",
            "application_id": "example-app",
            "adapter_id": "ad",
            "capability": "ex.n024_create",
            "permissions": ["ui.read"],
            "authentication_required": True,
            # Hostile: try to sneak VERIFIED on create
            "status": "VERIFIED",
            "verification_id": "forged",
            "last_verified_at": time.time(),
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "PARTIAL"
    assert body.get("verification_id") in (None, "")
    stored = registry.get(body["id"])
    assert stored is not None
    assert stored.status is ApiStatus.PARTIAL


def test_rest_create_requires_auth(client, registry):
    r = client.post(
        "/v1/apis",
        json={
            "name": "No Auth",
            "method": "POST",
            "path": "/v1/apps/example/n024-noauth",
            "source": "virtual_adapter",
            "capability": "ex.n024_noauth",
        },
    )
    assert r.status_code in (401, 403), r.text


def test_rest_create_rejected_payload_422(client, admin_headers, registry):
    r = client.post(
        "/v1/apis",
        headers=admin_headers,
        json={
            "name": "",
            "method": "POST",
            "path": "/v1/apps/example/n024-bad",
            "source": "virtual_adapter",
            "capability": "ex.n024_bad",
        },
    )
    assert r.status_code == 422, r.text


def test_verify_fail_stays_partial_not_verified(client, admin_headers, auth_headers, registry):
    created = client.post(
        "/v1/apis",
        headers=admin_headers,
        json={
            "name": "N024 Fail Verify",
            "method": "POST",
            "path": "/v1/apps/example/n024-fail-verify",
            "source": "virtual_adapter",
            "application_id": "example-app",
            "capability": "ex.n024_fail",
            "permissions": [],
        },
    )
    assert created.status_code == 200, created.text
    api_id = created.json()["id"]

    t = client.post(
        f"/v1/apis/{api_id}/test",
        headers=auth_headers,
        json={"params": {}, "update_registry_on_pass": False},
    )
    assert t.status_code == 200, t.text
    body = t.json()
    assert body.get("success") is not True
    stored = registry.get(api_id)
    assert stored is not None
    assert stored.status is ApiStatus.PARTIAL
    assert stored.status is not ApiStatus.VERIFIED


def test_publish_without_evidence_409(client, admin_headers, registry):
    rec = registry.register(
        _base(
            name="No Evidence",
            path="/v1/apps/example/n024-no-ev",
            capability="ex.n024_no_ev",
            status="PARTIAL",
        )
    )
    r = client.post(f"/v1/apis/{rec.id}/publish", headers=admin_headers)
    assert r.status_code == 409, r.text
    assert registry.get(rec.id).status is ApiStatus.PARTIAL


def test_create_simulate_evidence_publish_verified(client, admin_headers, registry):
    created = client.post(
        "/v1/apis",
        headers=admin_headers,
        json={
            "name": "N024 Publish Path",
            "method": "POST",
            "path": "/v1/apps/example/n024-publish",
            "source": "virtual_adapter",
            "application_id": "example-app",
            "capability": "ex.n024_publish",
            "permissions": ["ui.read"],
        },
    )
    assert created.status_code == 200, created.text
    api_id = created.json()["id"]
    assert created.json()["status"] == "PARTIAL"

    # Simulate successful verify evidence without going through live UI postcondition
    payload = registry.get(api_id).to_dict()
    payload["status"] = "PARTIAL"
    payload["verification_id"] = issue_verification_proof("ver_n024_sim")
    payload["last_verified_at"] = time.time()
    registry.register(payload)
    mid = registry.get(api_id)
    assert mid.status is ApiStatus.PARTIAL
    assert mid.verification_id == "ver_n024_sim"

    pub = client.post(f"/v1/apis/{api_id}/publish", headers=admin_headers)
    assert pub.status_code == 200, pub.text
    assert pub.json()["status"] == "VERIFIED"
    assert registry.get(api_id).status is ApiStatus.VERIFIED


def test_disable_after_publish_and_try_fail_closed(client, admin_headers, auth_headers, registry):
    rec = registry.register(
        _base(
            name="N024 Disable",
            path="/v1/apps/example/n024-dis",
            capability="ex.n024_dis",
            status="VERIFIED",
            verification_id=issue_verification_proof("ver_n024_dis"),
            last_verified_at=time.time(),
        )
    )
    d = client.post(f"/v1/apis/{rec.id}/disable", headers=admin_headers)
    assert d.status_code == 200, d.text
    assert d.json()["status"] == "DISABLED"

    # Publish while DISABLED must 409
    p = client.post(f"/v1/apis/{rec.id}/publish", headers=admin_headers)
    assert p.status_code == 409, p.text

    t = client.post(
        f"/v1/apis/{rec.id}/test",
        headers=auth_headers,
        json={"params": {}, "update_registry_on_pass": False},
    )
    if t.status_code == 200:
        body = t.json()
        assert body.get("success") is not True
        assert (body.get("verification") or {}).get("verified") is not True
    else:
        assert t.status_code in (401, 403, 404, 409, 422, 503), t.text


def test_run_api_test_pass_records_partial_evidence(registry):
    """When update_registry_on_pass=False and PASS, keep PARTIAL + evidence."""
    from windows_os_api.apps.adapters.engine import (
        create_adapter,
        get_adapter,
        reset_adapters,
    )

    reset_adapters()
    app = "n024-ev-app"
    create_adapter(app, hwnd=1001)
    edit = next(a for a in get_adapter(app).actions if a.control_type == "Edit")
    rec = registry.register(
        _base(
            name=edit.name,
            path=f"/v1/apps/{app}/actions/{edit.name}",
            application_id=app,
            capability=f"{app}.{edit.name}",
            status="PARTIAL",
        )
    )
    out = run_api_test(
        rec.id,
        params={"value": "n024-ev-val"},
        registry=registry,
        update_registry_on_pass=False,
    )
    assert out["success"] is True, out
    assert out.get("registry_updated") is False
    assert out.get("evidence_recorded") is True
    assert out["verification_id"]
    stored = registry.get(rec.id)
    assert stored is not None
    assert stored.status is ApiStatus.PARTIAL
    assert stored.verification_id == out["verification_id"]
    assert stored.last_verified_at is not None


def test_hostile_metadata_markers_still_present(client, admin_headers, registry):
    """XSS-ish name must not break HTML contract markers (textContent path)."""
    r = client.post(
        "/v1/apis",
        headers=admin_headers,
        json={
            "name": '<img src=x onerror=alert(1)>',
            "method": "POST",
            "path": "/v1/apps/example/n024-xss",
            "description": "<script>alert(1)</script>",
            "source": "virtual_adapter",
            "capability": "ex.n024_xss",
            "permissions": [],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PARTIAL"
    html = _html(client)
    assert "textContent" in html
    assert "cvpCreate" in html
    assert 'id="key" value=""' in html or re.search(r'id="key"[^>]*value=""', html)
