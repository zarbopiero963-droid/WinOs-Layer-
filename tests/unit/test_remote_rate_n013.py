"""N013 / H63-N013 — rate limit, body cap, concurrency, remote confine.

Unit/security only. Full installed W/L H63-N013 is MANUAL (#21).
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from windows_os_api.core.runtime.app import create_app
from windows_os_api.core.runtime.config import Settings, get_settings
from windows_os_api.core.security.rate_limit import ConcurrencyGate, RateLimiter
from windows_os_api.api.rest.deps import reset_limiter


@pytest.fixture()
def tight_client(tmp_sandbox, monkeypatch):
    monkeypatch.setenv("WINOS_API_KEYS", '["dev-key-change-me"]')
    monkeypatch.setenv("WINOS_ADMIN_API_KEYS", '["admin-key-change-me"]')
    monkeypatch.setenv("WINOS_RATE_LIMIT_PER_MINUTE", "5")
    monkeypatch.setenv("WINOS_MAX_BODY_BYTES", "64")
    monkeypatch.setenv("WINOS_MAX_CONCURRENT_REQUESTS", "2")
    monkeypatch.setenv("WINOS_REMOTE_ACCESS_ENABLED", "false")
    get_settings.cache_clear()
    reset_limiter()
    app = create_app(get_settings())
    with TestClient(app) as c:
        yield c
    reset_limiter()
    get_settings.cache_clear()


def test_rate_limiter_unit():
    lim = RateLimiter(2)
    assert lim.allow("x") and lim.allow("x")
    assert lim.allow("x") is False


def test_concurrency_gate_unit():
    g = ConcurrencyGate(2)
    assert g.try_acquire() and g.try_acquire()
    assert g.try_acquire() is False
    g.release()
    assert g.try_acquire() is True
    g.release()
    g.release()


def test_flood_returns_429(tight_client):
    h = {"X-API-Key": "dev-key-change-me"}
    codes = []
    for _ in range(8):
        codes.append(tight_client.get("/v1/system", headers=h).status_code)
    assert 429 in codes
    assert codes.count(200) >= 1  # some succeeded before limit


def test_body_cap_413(tight_client):
    h = {"X-API-Key": "admin-key-change-me", "Content-Type": "application/json"}
    # Content-Length implied by body > 64 bytes
    big = {"manifest": {"x": "y" * 80}}
    r = tight_client.post("/v1/trust/sign", headers=h, json=big)
    assert r.status_code == 413
    assert "large" in r.json()["detail"].lower()


def test_forwarded_loopback_claim_from_non_loopback_is_ignored_for_grant(tmp_sandbox, monkeypatch):
    """Peer is testclient (allowlisted); spoof header must not be the grant path.

    Build a custom ASGI receive isn't needed: we assert the policy flag and that
    a request with X-Forwarded-For still authenticates only via real peer rules.
    """
    monkeypatch.setenv("WINOS_API_KEYS", '["dev-key-change-me"]')
    monkeypatch.setenv("WINOS_REMOTE_ACCESS_ENABLED", "false")
    get_settings.cache_clear()
    reset_limiter()
    app = create_app(get_settings())
    with TestClient(app) as c:
        h = {
            "X-API-Key": "dev-key-change-me",
            "X-Forwarded-For": "127.0.0.1",
        }
        # testclient peer is allowlisted → 200; policy documents no trust
        assert c.get("/v1/system", headers=h).status_code == 200
        pol = c.get("/v1/remote/policy", headers=h).json()
        assert pol["forwarded_headers_trusted_for_loopback"] is False
        assert pol["max_body_bytes"] == get_settings().max_body_bytes


def test_forwarded_claim_rejected_for_foreign_peer():
    """Direct unit of the middleware decision helper via Settings + app factory.

    Simulate by invoking the middleware's peer check logic: a non-loopback peer
    with remote disabled is 403 regardless of X-Forwarded-For.
    """
    from starlette.requests import Request
    from starlette.responses import Response
    import asyncio

    settings = Settings(remote_access_enabled=False, api_keys=["k"], require_auth=False)
    app = create_app(settings)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/v1/system",
        "raw_path": b"/v1/system",
        "query_string": b"",
        "headers": [
            (b"x-forwarded-for", b"127.0.0.1"),
            (b"host", b"example.com"),
        ],
        "client": ("203.0.113.10", 12345),
        "server": ("127.0.0.1", 8765),
    }

    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    async def run():
        await app(scope, receive, send)

    asyncio.run(run())
    # First message should be http.response.start with 403
    starts = [m for m in messages if m.get("type") == "http.response.start"]
    assert starts, messages
    assert starts[0]["status"] == 403


def test_cors_not_star_when_remote(monkeypatch, tmp_sandbox):
    monkeypatch.setenv("WINOS_REMOTE_ACCESS_ENABLED", "true")
    monkeypatch.setenv("WINOS_CORS_ALLOWED_ORIGINS", '["https://cc.example"]')
    monkeypatch.setenv("WINOS_API_KEYS", '["dev-key-change-me"]')
    get_settings.cache_clear()
    reset_limiter()
    s = get_settings()
    assert s.remote_access_enabled is True
    assert s.cors_allowed_origins == ["https://cc.example"]
    app = create_app(s)
    # Inspect middleware config
    cors = [m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
    assert cors
    kwargs = cors[0].kwargs
    assert "*" not in kwargs.get("allow_origins", [])
    assert "https://cc.example" in kwargs["allow_origins"]


def test_remote_policy_exposes_limits(client, auth_headers):
    r = client.get("/v1/remote/policy", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["forwarded_headers_trusted_for_loopback"] is False
    assert "max_body_bytes" in body
    assert "rate_limit_per_minute" in body
