"""N012 / H63-N012 — revoke, rotate, user/app/session isolation.

Unit/security matrix only. Full installed W/L H63-N012 is MANUAL (#21).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

from windows_os_api.core.permissions.model import Role
from windows_os_api.core.runtime.app import create_app
from windows_os_api.core.runtime.config import Settings, get_settings
from windows_os_api.core.security.auth import (
    assert_active,
    build_auth_context,
    ensure_app_access,
    get_auth_registry,
    reset_auth_registry,
    resolve_role,
)
from windows_os_api.apps.workflows import recorder


@pytest.fixture()
def dual_client(tmp_sandbox, monkeypatch):
    """Client with two distinct automator keys for isolation tests."""
    monkeypatch.setenv(
        "WINOS_API_KEYS",
        '["user-a-key-n012xxxxxxxx", "user-b-key-n012xxxxxxxx"]',
    )
    monkeypatch.setenv("WINOS_ADMIN_API_KEYS", '["admin-key-change-me"]')
    get_settings.cache_clear()
    reset_auth_registry()
    settings = get_settings()
    app = create_app(settings)
    with TestClient(app) as c:
        yield c
    reset_auth_registry()
    get_settings.cache_clear()


def test_runtime_revoke_denies_while_key_still_in_settings():
    reset_auth_registry()
    s = Settings(api_keys=["keep-configured-key"], require_auth=True)
    assert resolve_role("keep-configured-key", s) == Role.AUTOMATOR
    get_auth_registry().revoke_key("keep-configured-key")
    with pytest.raises(HTTPException) as ei:
        resolve_role("keep-configured-key", s)
    assert ei.value.status_code == 401
    assert "revoked" in ei.value.detail.lower()


def test_rotate_revokes_old_and_accepts_new_overlay():
    reset_auth_registry()
    s = Settings(api_keys=["old-key-n012-aaaa"], admin_api_keys=["admin-k"], require_auth=True)
    assert resolve_role("old-key-n012-aaaa", s) == Role.AUTOMATOR
    out = get_auth_registry().rotate_key("old-key-n012-aaaa", "brand-new-key-n012", Role.AUTOMATOR)
    assert out["rotated"] is True
    with pytest.raises(HTTPException):
        resolve_role("old-key-n012-aaaa", s)
    assert resolve_role("brand-new-key-n012", s) == Role.AUTOMATOR


def test_assert_active_fails_after_revoke():
    reset_auth_registry()
    s = Settings(api_keys=["live-key-n012yyyyyyyy"], require_auth=True)
    ctx = build_auth_context("live-key-n012yyyyyyyy", s)
    assert_active(ctx)  # ok
    get_auth_registry().revoke_key("live-key-n012yyyyyyyy")
    with pytest.raises(HTTPException) as ei:
        assert_active(ctx)
    assert ei.value.status_code == 401


def test_app_scope_isolation():
    reset_auth_registry()
    s = Settings(api_keys=["scoped-key-n012zzzzzzzz"], require_auth=True)
    get_auth_registry().set_app_scopes("scoped-key-n012zzzzzzzz", ["app-allowed"])
    ctx = build_auth_context("scoped-key-n012zzzzzzzz", s)
    ensure_app_access(ctx, "app-allowed")
    with pytest.raises(HTTPException) as ei:
        ensure_app_access(ctx, "app-other")
    assert ei.value.status_code == 403


def test_admin_revoke_and_non_admin_forbidden(client, auth_headers, admin_headers):
    # Non-admin cannot revoke
    r = client.post(
        "/v1/auth/revoke",
        headers=auth_headers,
        json={"api_key": "viewer-key-n011"},
    )
    assert r.status_code == 403
    # Admin can revoke viewer key
    r = client.post(
        "/v1/auth/revoke",
        headers=admin_headers,
        json={"api_key": "viewer-key-n011"},
    )
    assert r.status_code == 200
    assert r.json()["revoked"] is True
    assert client.get("/v1/auth/me", headers={"X-API-Key": "viewer-key-n011"}).status_code == 401


def test_auth_me_exposes_session_and_scopes(client, auth_headers, admin_headers):
    # Bind scopes as admin
    r = client.put(
        "/v1/auth/scopes",
        headers=admin_headers,
        json={"api_key": "dev-key-change-me", "app_ids": ["crm-a"], "user_id": "user-dev"},
    )
    assert r.status_code == 200
    me = client.get("/v1/auth/me", headers=auth_headers)
    assert me.status_code == 200
    body = me.json()
    assert body["session_id"]
    assert body["user_id"] == "user-dev"
    assert body["app_scopes"] == ["crm-a"]
    assert body["key_fingerprint"]


def test_cross_user_workflow_isolation(dual_client):
    c = dual_client
    ha = {"X-API-Key": "user-a-key-n012xxxxxxxx"}
    hb = {"X-API-Key": "user-b-key-n012xxxxxxxx"}
    # A records a workflow
    start = c.post("/v1/workflows/record/start", headers=ha, json={"name": "wf-a", "app_id": "app-a"})
    assert start.status_code == 200
    wf_id = start.json()["id"]
    assert start.json()["owner_subject"].startswith("key:")
    c.post("/v1/workflows/record/step", headers=ha, json={"action": "noop", "params": {}})
    c.post("/v1/workflows/record/stop", headers=ha)
    # B must not see A's workflow in list
    listed = c.get("/v1/workflows", headers=hb).json()["workflows"]
    assert all(w["id"] != wf_id for w in listed)
    # B cannot play A's workflow
    play = c.post(f"/v1/workflows/{wf_id}/play", headers=hb)
    assert play.status_code == 403


def test_revoke_mid_workflow_denies_next_step(dual_client, monkeypatch):
    """Revoke during play → subsequent step denied at side-effect gate."""
    c = dual_client
    ha = {"X-API-Key": "user-a-key-n012xxxxxxxx"}
    admin = {"X-API-Key": "admin-key-change-me"}
    start = c.post(
        "/v1/workflows/record/start",
        headers=ha,
        json={"name": "wf-revoke", "app_id": "app-a"},
    )
    wf_id = start.json()["id"]
    c.post("/v1/workflows/record/step", headers=ha, json={"action": "step_one", "params": {}})
    c.post("/v1/workflows/record/step", headers=ha, json={"action": "step_two", "params": {}})
    c.post("/v1/workflows/record/stop", headers=ha)

    calls = {"n": 0}

    def flaky_invoke(app_id, action, params):
        calls["n"] += 1
        if calls["n"] == 1:
            # After first successful side-effect, admin revokes the caller's key.
            get_auth_registry().revoke_key("user-a-key-n012xxxxxxxx")
            return {"ok": True}
        return {"ok": True}

    # Drive player with before_step gate mirroring the REST path.
    from windows_os_api.core.security.auth import build_auth_context, assert_active, ensure_app_access

    settings = get_settings()
    # Build context *before* revoke (simulates in-flight request principal).
    ctx = build_auth_context("user-a-key-n012xxxxxxxx", settings)
    wf = recorder.get_workflow(wf_id)

    def gate(_i, _s):
        assert_active(ctx)
        ensure_app_access(ctx, wf.app_id)

    # First step runs, revoke happens inside invoke; second step gate must fail.
    # Re-structure: revoke between steps via before_step on step index 1.
    step_count = {"i": 0}

    def gate2(index, _s):
        if index == 1:
            get_auth_registry().revoke_key("user-a-key-n012xxxxxxxx")
        assert_active(ctx)
        ensure_app_access(ctx, wf.app_id)

    result = recorder.play(wf_id, flaky_invoke, before_step=gate2)
    assert result["ok"] is False
    assert result["failed_step"] == 1
    assert result["results"][1].get("denied") is True
    # Reconnect with same key is denied
    with pytest.raises(HTTPException) as ei:
        build_auth_context("user-a-key-n012xxxxxxxx", settings)
    assert ei.value.status_code == 401


def test_http_play_denied_after_key_revoke(dual_client):
    c = dual_client
    ha = {"X-API-Key": "user-a-key-n012xxxxxxxx"}
    admin = {"X-API-Key": "admin-key-change-me"}
    start = c.post(
        "/v1/workflows/record/start",
        headers=ha,
        json={"name": "wf-http", "app_id": "app-a"},
    )
    wf_id = start.json()["id"]
    c.post("/v1/workflows/record/step", headers=ha, json={"action": "x", "params": {}})
    c.post("/v1/workflows/record/stop", headers=ha)
    assert c.post("/v1/auth/revoke", headers=admin, json={"api_key": "user-a-key-n012xxxxxxxx"}).status_code == 200
    # Reconnect / play with revoked key → 401 at authenticate
    assert c.post(f"/v1/workflows/{wf_id}/play", headers=ha).status_code == 401


def test_cross_app_invoke_denied_when_scoped(dual_client):
    c = dual_client
    admin = {"X-API-Key": "admin-key-change-me"}
    ha = {"X-API-Key": "user-a-key-n012xxxxxxxx"}
    assert c.put(
        "/v1/auth/scopes",
        headers=admin,
        json={"api_key": "user-a-key-n012xxxxxxxx", "app_ids": ["only-this-app"]},
    ).status_code == 200
    # Invoke outside scope → 403 (FakeBackend may 404 adapter first — scope checked first)
    r = c.post(
        "/v1/apps/other-app/actions/do_thing",
        headers=ha,
        json={"params": {}},
    )
    assert r.status_code == 403
    assert "scope" in r.json()["detail"].lower()


def test_self_session_revoke(client, auth_headers):
    me = client.get("/v1/auth/me", headers=auth_headers).json()
    sid = me["session_id"]
    r = client.post("/v1/auth/revoke-session", headers=auth_headers, json={"session_id": sid})
    assert r.status_code == 200
    # Same session id is revoked; a new /auth/me still works (new session) because key live
    me2 = client.get("/v1/auth/me", headers=auth_headers)
    assert me2.status_code == 200
    assert me2.json()["session_id"] != sid


def test_rotate_http_admin(client, admin_headers):
    r = client.post(
        "/v1/auth/rotate",
        headers=admin_headers,
        json={"old_api_key": "dev-key-change-me", "new_api_key": "rotated-automator-n012"},
    )
    assert r.status_code == 200
    assert r.json()["rotated"] is True
    assert client.get("/v1/auth/me", headers={"X-API-Key": "dev-key-change-me"}).status_code == 401
    me = client.get("/v1/auth/me", headers={"X-API-Key": "rotated-automator-n012"})
    assert me.status_code == 200
    assert me.json()["role"] == "automator"
