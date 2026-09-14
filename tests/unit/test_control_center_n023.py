"""N023 / H63-N023 — Control Center Try it, diagnose, disable.

Unit/API matrix (Q08/Q13). Full installed W/L H63-N023 is MANUAL (#21).
Coverage refs: R40 W092 L092 S61-11 S61-25 G28 G29.
"""
from __future__ import annotations

import re
import time

import pytest

from windows_os_api.apps.api_registry import reset_api_registry
from windows_os_api.apps.api_registry.model import ApiStatus


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


def test_control_center_n023_try_it_markers(client):
    html = _html(client)
    assert "Try it" in html or "API Test" in html
    assert "/v1/apis/" in html
    assert "/test" in html
    # Path pattern used by Try it against N018 gateway
    assert re.search(r"/v1/apis/.*test|apis/.*\+.*test|/test", html)
    assert "POST" in html  # method for try-it


def test_control_center_n023_diagnose_markers(client):
    html = _html(client)
    assert "/v1/workflows" in html
    assert "/v1/apps/adapters/list" in html
    assert "/v1/audit" in html
    assert "View workflow" in html or "viewWorkflow" in html
    assert "View adapter" in html or "viewAdapter" in html
    assert "View log" in html or "viewLog" in html or "viewAudit" in html
    assert "Disable" in html
    assert "/disable" in html or "disableApi" in html


def test_control_center_n023_api_key_not_prefilled(client):
    html = _html(client)
    assert re.search(r'id="key"[^>]*value=""', html) or 'id="key" value=""' in html
    assert 'value="dev"' not in html
    assert 'value="admin"' not in html
    assert "dev-key-change-me" not in html
    assert "admin-key-change-me" not in html


def test_control_center_n023_textcontent_for_results(client):
    html = _html(client)
    assert "textContent" in html
    # Results / errors must not use innerHTML with raw API bodies
    assert not re.search(
        r"innerHTML\s*=\s*[^;]*(r\.body|result|errorBody|rec\.name|rec\.description)",
        html,
    )


def test_rest_disable_requires_auth_and_sets_disabled(client, admin_headers, auth_headers, registry):
    rec = registry.register(
        _base(
            name="To Disable",
            path="/v1/apps/example/to-disable",
            capability="ex.disable",
            status="VERIFIED",
            verification_id="v-n023-dis",
            last_verified_at=time.time(),
        )
    )
    # Missing key → 401/403
    bare = client.post(f"/v1/apis/{rec.id}/disable")
    assert bare.status_code in (401, 403), bare.text

    # Operator/dev key may lack ADAPTER_MANAGE — admin must succeed
    r = client.post(f"/v1/apis/{rec.id}/disable", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == rec.id
    assert body["status"] == "DISABLED"

    stored = registry.get(rec.id)
    assert stored is not None
    assert stored.status is ApiStatus.DISABLED


def test_rest_disable_missing_api_404(client, admin_headers, registry):
    r = client.post("/v1/apis/does-not-exist-n023/disable", headers=admin_headers)
    assert r.status_code == 404, r.text


def test_try_it_after_disable_not_success(client, admin_headers, auth_headers, registry):
    """After disable, Try it / API Test must not report verified success."""
    rec = registry.register(
        _base(
            name="Disable Then Try",
            path="/v1/apps/example/disable-try",
            capability="ex.disable_try",
            status="VERIFIED",
            verification_id="v-n023-try",
            last_verified_at=time.time(),
        )
    )
    d = client.post(f"/v1/apis/{rec.id}/disable", headers=admin_headers)
    assert d.status_code == 200, d.text

    t = client.post(
        f"/v1/apis/{rec.id}/test",
        headers=auth_headers,
        json={"params": {}, "update_registry_on_pass": False},
    )
    # May be 200 with success=false (gateway blocked) or 4xx — never fake success
    if t.status_code == 200:
        body = t.json()
        assert body.get("success") is not True
        ver = body.get("verification") or {}
        assert ver.get("verified") is not True
    else:
        assert t.status_code in (401, 403, 404, 409, 422, 503), t.text


def test_try_it_missing_key_is_error(client, registry):
    rec = registry.register(
        _base(
            name="Need Key",
            path="/v1/apps/example/need-key",
            capability="ex.need_key",
            status="VERIFIED",
            verification_id="v-n023-key",
            last_verified_at=time.time(),
        )
    )
    r = client.post(f"/v1/apis/{rec.id}/test", json={"params": {}})
    assert r.status_code in (401, 403), r.text
