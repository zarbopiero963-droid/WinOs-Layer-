"""N022 / H63-N022 — Control Center tabs + VERIFIED-only catalog UI.

Unit/API matrix (Q13). Full installed W/L H63-N022 is MANUAL (#21).
Coverage refs: R40 W092 L092 S61-10–S61-11 S61-25 G28 G29.
"""
from __future__ import annotations

import re
import time

import pytest

from windows_os_api.apps.api_registry import reset_api_registry
from windows_os_api.apps.api_registry.catalog import list_catalog


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


def test_control_center_has_tab_markers(client):
    html = _html(client)
    for tab in ("dashboard", "os", "apps", "api"):
        assert f'data-tab="{tab}"' in html, f"missing data-tab={tab}"


def test_control_center_references_verified_apis_catalog(client):
    html = _html(client)
    assert "/v1/apis" in html
    assert "status=VERIFIED" in html or 'status", "VERIFIED"' in html or "status=VERIFIED" in html
    # Explicit VERIFIED-only contract visible in source
    assert "VERIFIED" in html
    assert re.search(r"status[=\"']\s*VERIFIED|status=VERIFIED|\"status\",\s*\"VERIFIED\"", html)


def test_control_center_api_key_not_prefilled_dev_admin(client):
    html = _html(client)
    # Empty value on the Control Center API key field
    assert re.search(r'id="key"[^>]*value=""', html) or 'id="key" value=""' in html
    assert not re.search(r'id="key"[^>]*value="(dev|admin|dev-key|admin-key)', html)
    # No hardcoded default key literals as input values
    assert 'value="dev"' not in html
    assert 'value="admin"' not in html
    assert "dev-key-change-me" not in html
    assert "admin-key-change-me" not in html


def test_control_center_uses_textcontent_or_escape_not_innerhtml_for_meta(client):
    html = _html(client)
    assert "textContent" in html or "escapeHtml" in html
    # Must not pipe hostile API name/description through innerHTML
    # Allow generic DOM usage elsewhere only if name/description use textContent.
    # Contract: source contains textContent assignment pattern for detail/name.
    assert "textContent" in html
    # No assignment of rec.name / rec.description via innerHTML
    assert not re.search(
        r"innerHTML\s*=\s*[^;]*(rec\.name|rec\.description|api\.name|api\.description)",
        html,
    )
    assert not re.search(
        r"\.innerHTML\s*=\s*[`'\"][^`'\"]*\$\{[^}]*(name|description)",
        html,
    )


def test_verified_filter_still_excludes_partial(client, auth_headers, registry):
    """Optional: registry VERIFIED vs PARTIAL — REST filter used by the UI."""
    verified = registry.register(
        _base(
            name="Verified One",
            path="/v1/apps/example/verified",
            capability="ex.verified",
            status="VERIFIED",
            verification_id="v-n022",
            last_verified_at=time.time(),
        )
    )
    partial = registry.register(
        _base(
            name="Partial Only",
            path="/v1/apps/example/partial",
            capability="ex.partial",
            status="PARTIAL",
        )
    )
    page = list_catalog(registry=registry, status="VERIFIED", limit=100)
    ids = {r.id for r in page.items}
    assert verified.id in ids
    assert partial.id not in ids

    r = client.get(
        "/v1/apis",
        headers=auth_headers,
        params={"status": "VERIFIED", "application_id": "example-app"},
    )
    assert r.status_code == 200, r.text
    body_ids = {a["id"] for a in r.json()["apis"]}
    assert verified.id in body_ids
    assert partial.id not in body_ids
    for a in r.json()["apis"]:
        assert a["status"] == "VERIFIED"


def test_control_center_mentions_adapter_status_labels(client):
    html = _html(client)
    assert "nessuno" in html
    assert "bound" in html
    assert "unbound" in html
    assert "/v1/apps/discover" in html
    assert "/v1/apps/adapters/list" in html
