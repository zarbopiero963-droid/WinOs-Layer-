"""N011 / H63-N011 — identity & assignable roles on all ingresses.

Unit/security matrix only. Full installed W/L H63-N011 is MANUAL (#21).
"""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from windows_os_api.core.permissions.model import Permission, Role
from windows_os_api.core.runtime.app import create_app
from windows_os_api.core.runtime.config import Settings, get_settings
from windows_os_api.core.security.auth import (
    PLACEHOLDER_API_KEYS,
    build_auth_context,
    resolve_role,
)


def _clear_winos_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("WINOS_"):
            monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()


def test_release_defaults_have_no_baked_in_keys(monkeypatch):
    _clear_winos_env(monkeypatch)
    s = Settings()
    assert s.api_keys == []
    assert s.admin_api_keys == []
    assert s.viewer_api_keys == []
    assert s.operator_api_keys == []
    assert s.require_auth is True
    # Placeholders are not configured → 401 on release defaults
    from fastapi import HTTPException
    from windows_os_api.core.security.auth import resolve_role, PLACEHOLDER_API_KEYS
    for bad in PLACEHOLDER_API_KEYS:
        try:
            resolve_role(bad, s)
            raise AssertionError(f"expected 401 for {bad}")
        except HTTPException as ei:
            assert ei.status_code == 401


def test_release_path_rejects_unconfigured_and_unknown_keys():
    # Explicit real keys configured — placeholders / unknowns still 401
    # because they are absent from every role list (release-safe).
    s = Settings(
        api_keys=["real-automator-key-0001"],
        admin_api_keys=["real-admin-key-0001"],
        viewer_api_keys=["real-viewer-key-0001"],
        operator_api_keys=["real-operator-key-0001"],
        require_auth=True,
    )
    for bad in list(PLACEHOLDER_API_KEYS) + ["", "nope-unknown"]:
        with pytest.raises(HTTPException) as ei:
            resolve_role(bad, s)
        assert ei.value.status_code == 401


def test_four_roles_assignable_via_distinct_keys():
    s = Settings(
        viewer_api_keys=["k-viewer"],
        operator_api_keys=["k-operator"],
        api_keys=["k-automator"],
        admin_api_keys=["k-admin"],
        require_auth=True,
    )
    assert resolve_role("k-viewer", s) == Role.VIEWER
    assert resolve_role("k-operator", s) == Role.OPERATOR
    assert resolve_role("k-automator", s) == Role.AUTOMATOR
    assert resolve_role("k-admin", s) == Role.ADMIN


def test_ambiguous_key_in_two_lists_rejected():
    s = Settings(
        api_keys=["shared-key"],
        admin_api_keys=["shared-key"],
        require_auth=True,
    )
    with pytest.raises(HTTPException) as ei:
        resolve_role("shared-key", s)
    assert ei.value.status_code == 401


def test_no_auth_is_anonymous_viewer_not_admin():
    s = Settings(require_auth=False)
    ctx = build_auth_context(None, s)
    assert ctx.subject == "anonymous"
    assert ctx.role == Role.VIEWER
    assert ctx.role != Role.ADMIN
    assert Permission.ADMIN not in ctx.permissions
    assert not ctx.check(Permission.ADMIN)
    assert ctx.check(Permission.SYSTEM_READ)


def test_allow_deny_matrix_http(client, viewer_headers, operator_headers, auth_headers, admin_headers):
    assert client.get("/v1/system", headers=viewer_headers).status_code == 200
    assert client.post("/v1/system/power/lock", headers=viewer_headers).status_code == 403
    assert client.get("/v1/system", headers=operator_headers).status_code == 200
    assert client.post("/v1/system/power/lock", headers=operator_headers).status_code == 403
    assert client.post("/v1/system/power/lock", headers=auth_headers).status_code == 403
    me = client.get("/v1/auth/me", headers=admin_headers)
    assert me.status_code == 200
    assert me.json()["role"] == "admin"


def test_invalid_key_and_missing_key(client):
    assert client.get("/v1/system", headers={"X-API-Key": "totally-invalid"}).status_code == 401
    assert client.get("/v1/system").status_code == 401


def test_audit_without_key_is_401(client):
    assert client.get("/v1/audit").status_code == 401


def test_auth_me_four_credentials(client):
    expected = {
        "viewer-key-n011": "viewer",
        "operator-key-n011": "operator",
        "dev-key-change-me": "automator",
        "admin-key-change-me": "admin",
    }
    for key, role in expected.items():
        r = client.get("/v1/auth/me", headers={"X-API-Key": key})
        assert r.status_code == 200, key
        assert r.json()["role"] == role


def test_ws_rejects_invalid_key_same_as_rest(client):
    # N040: auth via header (query api_key is always denied — see test_websocket_n040).
    with pytest.raises((WebSocketDisconnect, Exception)):
        with client.websocket_connect(
            "/ws/events",
            headers={"X-API-Key": "not-a-real-key"},
        ) as ws:
            ws.receive_text()


def test_ws_accepts_valid_automator_key(client):
    # N040: header handshake (no secret in URL).
    with client.websocket_connect(
        "/ws/events",
        headers={"X-API-Key": "dev-key-change-me"},
    ) as ws:
        msg = ws.receive_text()
        assert "auth_ok" in msg


def test_build_auth_context_redacts_key_material():
    s = Settings(
        api_keys=["abcdefghijklmnop"],
        require_auth=True,
    )
    ctx = build_auth_context("abcdefghijklmnop", s)
    assert ctx.api_key == "abcdefgh..."
    assert "ijklmnop" not in ctx.api_key
    assert ctx.role == Role.AUTOMATOR
