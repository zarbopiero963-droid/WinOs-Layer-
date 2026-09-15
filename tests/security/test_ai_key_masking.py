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


@pytest.mark.security
def test_rotate_key_response_has_no_old_or_new_raw(client, admin_headers):
    old = "sk-old-n026-rotate-AAAAAAAA1111"
    new = "sk-new-n026-rotate-BBBBBBBB2222"
    r1 = client.put(
        "/v1/ai/settings",
        headers=admin_headers,
        json={"provider": "openai", "api_key": old},
    )
    assert r1.status_code == 200
    r2 = client.put(
        "/v1/ai/settings",
        headers=admin_headers,
        json={"api_key": new},
    )
    assert r2.status_code == 200
    blob = json.dumps(r1.json()) + json.dumps(r2.json())
    assert old not in blob
    assert new not in blob
    assert r2.json()["api_key_set"] is True
    assert r2.json()["api_key_preview"].endswith(new[-4:])


@pytest.mark.security
def test_persist_failure_http_detail_has_no_secret(client, admin_headers, monkeypatch):
    secret = "sk-persist-fail-n026-LEAKCHECK9999"
    # Establish a durable key first
    ok = client.put(
        "/v1/ai/settings",
        headers=admin_headers,
        json={"provider": "openai", "api_key": "sk-prior-durable-key-AAAAAAAA"},
    )
    assert ok.status_code == 200

    def boom(*_a, **_k):
        raise OSError("disk full with " + secret)

    monkeypatch.setattr("windows_os_api.apps.ai.settings_store.os.open", boom)
    r = client.put(
        "/v1/ai/settings",
        headers=admin_headers,
        json={"api_key": secret},
    )
    assert r.status_code == 500
    body = json.dumps(r.json())
    assert secret not in body
    assert "disk full" not in body.lower()
    assert "Failed to persist" in (r.json().get("detail") or "")
    # Prior key still present (no half-apply)
    got = client.get("/v1/ai/settings", headers=admin_headers)
    assert got.status_code == 200
    assert got.json()["api_key_set"] is True
    assert secret not in json.dumps(got.json())
    assert got.json()["api_key_preview"].endswith("AAAA")


@pytest.mark.security
def test_control_center_ai_key_not_prefilled_and_storage_copy(client):
    html = client.get("/").text
    assert 'id="ai_api_key"' in html
    assert 'value="dev"' not in html
    assert 'value="admin"' not in html
    assert "dev-key-change-me" not in html
    assert "admin-key-change-me" not in html
    # password field empty (explicit value="" or no value= with secret)
    assert 'id="ai_api_key"' in html
    assert "sk-proj-" not in html
    assert "owner-only" in html.lower() or "chmod 600" in html
