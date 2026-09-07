"""Security: AI settings never echo full API keys."""
from __future__ import annotations

import json

import pytest


@pytest.mark.security
def test_get_ai_settings_never_returns_full_key(client, admin_headers, tmp_sandbox):
    secret = "sk-secretKEY-do-not-leak-1234567890"
    put = client.put(
        "/v1/ai/settings",
        headers=admin_headers,
        json={
            "provider": "openai",
            "api_key": secret,
            "model": "gpt-4o-mini",
        },
    )
    assert put.status_code == 200
    body = put.json()
    assert body["api_key_set"] is True
    assert body["api_key_preview"] is not None
    assert secret not in json.dumps(body)
    assert "api_key" not in body or body.get("api_key") in (None, "")

    got = client.get("/v1/ai/settings", headers=admin_headers)
    assert got.status_code == 200
    g = got.json()
    assert g["api_key_set"] is True
    assert secret not in json.dumps(g)
    assert g["api_key_preview"].endswith(secret[-4:])
    assert "…" in g["api_key_preview"]


@pytest.mark.security
def test_ai_settings_requires_admin(client, auth_headers):
    assert client.get("/v1/ai/settings", headers=auth_headers).status_code == 403
    assert client.put("/v1/ai/settings", headers=auth_headers, json={"provider": "local"}).status_code == 403
    assert client.post("/v1/ai/test", headers=auth_headers, json={}).status_code == 403


@pytest.mark.security
def test_audit_log_has_no_raw_key(client, admin_headers):
    from windows_os_api.core.security.audit import get_audit_logger

    secret = "sk-audit-leak-check-ABCDEFGHijkl"
    client.put(
        "/v1/ai/settings",
        headers=admin_headers,
        json={"provider": "openai", "api_key": secret},
    )
    client.post("/v1/ai/test", headers=admin_headers, json={"spend": False})
    entries = get_audit_logger().read_all()
    blob = json.dumps(entries)
    assert secret not in blob
    assert any(e.get("action") == "ai.settings.put" for e in entries)
    put_entries = [e for e in entries if e.get("action") == "ai.settings.put"]
    assert put_entries
    detail = put_entries[-1].get("detail") or {}
    assert detail.get("api_key_set") is True
    assert detail.get("api_key_last4") == secret[-4:]


@pytest.mark.security
def test_clear_key_via_empty_string(client, admin_headers):
    client.put(
        "/v1/ai/settings",
        headers=admin_headers,
        json={"provider": "openai", "api_key": "sk-to-be-cleared-1234567890"},
    )
    cleared = client.put(
        "/v1/ai/settings",
        headers=admin_headers,
        json={"api_key": ""},
    )
    assert cleared.status_code == 200
    assert cleared.json()["api_key_set"] is False
    assert cleared.json()["api_key_preview"] is None


@pytest.mark.security
def test_ai_test_local_ok(client, admin_headers):
    client.put("/v1/ai/settings", headers=admin_headers, json={"provider": "local", "api_key": ""})
    r = client.post("/v1/ai/test", headers=admin_headers, json={"spend": False})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["skipped_network"] is True
