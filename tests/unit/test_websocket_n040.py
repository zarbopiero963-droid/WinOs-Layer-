"""N040 / H63-N040 — WebSocket auth, Origin, app/subject isolation, caps.

Unit-level on box. Full installed W/L H63-N040 is MANUAL_ONLY (#21).
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from windows_os_api.api.websocket import bus as ws_bus
from windows_os_api.api.websocket.bus import (
    encode_ws_subprotocol_key,
    event_visible_to,
    ws_origin_allowed,
)
from windows_os_api.core.events.bus import Event, get_event_bus, reset_event_bus
from windows_os_api.core.permissions.model import Role
from windows_os_api.core.runtime.app import create_app
from windows_os_api.core.runtime.config import Settings, get_settings
from windows_os_api.core.security.auth import (
    AuthContext,
    get_auth_registry,
    reset_auth_registry,
)


@pytest.fixture()
def ws_client(tmp_sandbox, monkeypatch):
    monkeypatch.setenv(
        "WINOS_API_KEYS",
        '["user-a-key-n040xxxxxxxx", "user-b-key-n040xxxxxxxx"]',
    )
    monkeypatch.setenv("WINOS_ADMIN_API_KEYS", '["admin-key-change-me"]')
    monkeypatch.setenv("WINOS_VIEWER_API_KEYS", '["viewer-key-n011"]')
    monkeypatch.setenv("WINOS_REMOTE_ACCESS_ENABLED", "false")
    get_settings.cache_clear()
    reset_auth_registry()
    reset_event_bus()
    app = create_app(get_settings())
    with TestClient(app) as c:
        yield c
    reset_auth_registry()
    reset_event_bus()
    get_settings.cache_clear()


def _recv_auth_ok(ws):
    msg = json.loads(ws.receive_text())
    assert msg["type"] == "auth_ok"
    return msg


def test_query_api_key_rejected(ws_client):
    with pytest.raises(WebSocketDisconnect) as ei:
        with ws_client.websocket_connect(
            "/ws/events?api_key=user-a-key-n040xxxxxxxx"
        ) as ws:
            ws.receive_text()
    assert ei.value.code == 4401


def test_header_auth_accepted(ws_client):
    with ws_client.websocket_connect(
        "/ws/events",
        headers={"X-API-Key": "user-a-key-n040xxxxxxxx"},
    ) as ws:
        ack = _recv_auth_ok(ws)
        assert ack["subject"].startswith("key:")


def test_first_message_auth_accepted(ws_client):
    with ws_client.websocket_connect("/ws/events") as ws:
        ws.send_text(
            json.dumps({"type": "auth", "api_key": "user-a-key-n040xxxxxxxx"})
        )
        ack = _recv_auth_ok(ws)
        assert ack["role"] == "automator"


def test_subprotocol_auth_accepted(ws_client):
    proto = encode_ws_subprotocol_key("user-a-key-n040xxxxxxxx")
    with ws_client.websocket_connect(
        "/ws/events",
        subprotocols=[proto],
    ) as ws:
        ack = _recv_auth_ok(ws)
        assert ack["type"] == "auth_ok"


def test_hostile_origin_denied_on_loopback(ws_client):
    with pytest.raises(WebSocketDisconnect) as ei:
        with ws_client.websocket_connect(
            "/ws/events",
            headers={
                "X-API-Key": "user-a-key-n040xxxxxxxx",
                "Origin": "https://evil.example",
            },
        ) as ws:
            ws.receive_text()
    assert ei.value.code == 4403


def test_local_origin_allowed(ws_client):
    with ws_client.websocket_connect(
        "/ws/events",
        headers={
            "X-API-Key": "user-a-key-n040xxxxxxxx",
            "Origin": "http://127.0.0.1:8765",
        },
    ) as ws:
        _recv_auth_ok(ws)


def test_remote_mode_requires_allowed_origin(tmp_sandbox, monkeypatch):
    monkeypatch.setenv("WINOS_API_KEYS", '["user-a-key-n040xxxxxxxx"]')
    monkeypatch.setenv("WINOS_REMOTE_ACCESS_ENABLED", "true")
    monkeypatch.setenv(
        "WINOS_CORS_ALLOWED_ORIGINS", '["https://cc.example"]'
    )
    get_settings.cache_clear()
    reset_auth_registry()
    reset_event_bus()
    app = create_app(get_settings())
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect) as ei:
            with c.websocket_connect(
                "/ws/events",
                headers={
                    "X-API-Key": "user-a-key-n040xxxxxxxx",
                    "Origin": "https://evil.example",
                },
            ) as ws:
                ws.receive_text()
        assert ei.value.code == 4403

        with c.websocket_connect(
            "/ws/events",
            headers={
                "X-API-Key": "user-a-key-n040xxxxxxxx",
                "Origin": "https://cc.example",
            },
        ) as ws:
            _recv_auth_ok(ws)
    get_settings.cache_clear()
    reset_auth_registry()
    reset_event_bus()


def test_app_scope_isolation_two_clients(ws_client):
    reg = get_auth_registry()
    reg.set_app_scopes("user-a-key-n040xxxxxxxx", ["app-a"])
    reg.set_app_scopes("user-b-key-n040xxxxxxxx", ["app-b"])

    with ws_client.websocket_connect(
        "/ws/events",
        headers={"X-API-Key": "user-a-key-n040xxxxxxxx"},
    ) as ws_a:
        with ws_client.websocket_connect(
            "/ws/events",
            headers={"X-API-Key": "user-b-key-n040xxxxxxxx"},
        ) as ws_b:
            _recv_auth_ok(ws_a)
            _recv_auth_ok(ws_b)

            bus = get_event_bus()
            bus.publish_sync(
                "agent.goal.proposed",
                {"goal": "x", "app_id": "app-a"},
                provenance="agent",
            )
            bus.publish_sync(
                "agent.goal.proposed",
                {"goal": "y", "app_id": "app-b"},
                provenance="agent",
            )
            bus.publish_sync(
                "system.probe",
                {"ok": True},
                provenance="system",
            )

            # Drain with short timeouts — each client should only see own app + unbound.
            def drain(ws, n=3, timeout=2.0):
                out = []
                end = time.time() + timeout
                while len(out) < n and time.time() < end:
                    try:
                        raw = ws.receive_text()
                    except WebSocketDisconnect:
                        break
                    msg = json.loads(raw)
                    if msg.get("type") == "auth_ok":
                        continue
                    out.append(msg)
                return out

            # Give the event loop a tick to fan out.
            time.sleep(0.05)
            got_a = drain(ws_a, n=2)
            got_b = drain(ws_b, n=2)

    apps_a = {
        (m.get("payload") or {}).get("app_id")
        for m in got_a
        if m.get("type") != "auth_ok"
    }
    apps_b = {
        (m.get("payload") or {}).get("app_id")
        for m in got_b
        if m.get("type") != "auth_ok"
    }
    # A must not see app-b; B must not see app-a.
    assert "app-b" not in apps_a
    assert "app-a" not in apps_b
    assert "app-a" in apps_a or None in apps_a  # own app and/or unbound
    assert "app-b" in apps_b or None in apps_b


def test_empty_scopes_fail_closed_on_app_events():
    """Non-admin empty app_scopes must not see app-bound events."""
    ctx = AuthContext(
        api_key="abcdefgh...",
        role=Role.AUTOMATOR,
        subject="key:abcdefgh",
        permissions=set(),
        key_fingerprint="fp",
        session_id="s",
        user_id="key:abcdefgh",
        app_scopes=frozenset(),
    )
    bound = Event(type="agent.goal.proposed", payload={"app_id": "crm"})
    unbound = Event(type="system.probe", payload={"ok": True})
    assert event_visible_to(ctx, bound) is False
    assert event_visible_to(ctx, unbound) is True


def test_admin_sees_all_apps():
    ctx = AuthContext(
        api_key="admin...",
        role=Role.ADMIN,
        subject="key:admin",
        permissions=set(),
        key_fingerprint="afp",
        session_id="s",
        user_id="key:admin",
        app_scopes=frozenset(),
    )
    bound = Event(type="agent.goal.proposed", payload={"app_id": "crm"})
    assert event_visible_to(ctx, bound) is True


def test_subject_isolation():
    ctx = AuthContext(
        api_key="abcdefgh...",
        role=Role.AUTOMATOR,
        subject="key:abcdefgh",
        permissions=set(),
        key_fingerprint="fp",
        session_id="s",
        user_id="user-a",
        app_scopes=frozenset(),
    )
    foreign = Event(
        type="workflow.recorded",
        payload={"owner_subject": "key:otherxxx"},
    )
    own = Event(
        type="workflow.recorded",
        payload={"owner_subject": "key:abcdefgh"},
    )
    assert event_visible_to(ctx, foreign) is False
    assert event_visible_to(ctx, own) is True


def test_revoke_closes_connection(ws_client):
    with ws_client.websocket_connect(
        "/ws/events",
        headers={"X-API-Key": "user-a-key-n040xxxxxxxx"},
    ) as ws:
        _recv_auth_ok(ws)
        get_auth_registry().revoke_key("user-a-key-n040xxxxxxxx")
        # Publish something so the loop wakes and checks revoke.
        get_event_bus().publish_sync(
            "system.probe", {"wake": True}, provenance="system"
        )
        with pytest.raises(WebSocketDisconnect) as ei:
            # May receive the event or the close depending on timing;
            # keep reading until disconnect.
            for _ in range(10):
                ws.receive_text()
        assert ei.value.code == 4401


def test_flood_closes_connection(ws_client, monkeypatch):
    monkeypatch.setattr(ws_bus, "WS_MAX_SENDS_PER_WINDOW", 5)
    monkeypatch.setattr(ws_bus, "WS_SEND_WINDOW_S", 60.0)
    with ws_client.websocket_connect(
        "/ws/events",
        headers={"X-API-Key": "admin-key-change-me"},
    ) as ws:
        _recv_auth_ok(ws)
        bus = get_event_bus()
        with pytest.raises(WebSocketDisconnect) as ei:
            for i in range(20):
                bus.publish_sync(
                    "system.probe",
                    {"n": i},
                    provenance="system",
                )
                # Force receive so sends happen on the server side.
                try:
                    ws.receive_text()
                except WebSocketDisconnect as e:
                    raise e
                time.sleep(0.01)
        assert ei.value.code == 1013


def test_cleanup_subscriber_count_after_disconnect(ws_client):
    bus = get_event_bus()
    before = bus.stats().subscribers
    with ws_client.websocket_connect(
        "/ws/events",
        headers={"X-API-Key": "user-a-key-n040xxxxxxxx"},
    ) as ws:
        _recv_auth_ok(ws)
        # Allow subscribe() to register.
        time.sleep(0.05)
        during = bus.stats().subscribers
        assert during >= before + 1
    time.sleep(0.05)
    after = bus.stats().subscribers
    assert after == before


def test_idle_timeout_closes(ws_client, monkeypatch):
    monkeypatch.setattr(ws_bus, "WS_IDLE_TIMEOUT_S", 0.15)
    with ws_client.websocket_connect(
        "/ws/events",
        headers={"X-API-Key": "user-a-key-n040xxxxxxxx"},
    ) as ws:
        _recv_auth_ok(ws)
        with pytest.raises(WebSocketDisconnect) as ei:
            # No events published → idle close.
            ws.receive_text()
        assert ei.value.code == 1001


def test_ws_origin_helper_unit():
    settings = Settings(
        remote_access_enabled=False,
        allowed_hosts=["127.0.0.1", "localhost"],
        port=8765,
    )

    class _Client:
        host = "testclient"

    class _WS:
        def __init__(self, origin=None, host="testclient"):
            self.headers = {}
            if origin:
                self.headers["origin"] = origin
            self.client = _Client()
            self.client.host = host

    assert ws_origin_allowed(_WS(), settings) is True
    assert ws_origin_allowed(_WS(origin="https://evil.example"), settings) is False
    assert ws_origin_allowed(_WS(origin="http://127.0.0.1:8765"), settings) is True

    remote = Settings(
        remote_access_enabled=True,
        cors_allowed_origins=["https://cc.example"],
        allowed_hosts=["127.0.0.1"],
        port=8765,
    )
    assert ws_origin_allowed(_WS(), remote) is False
    assert ws_origin_allowed(_WS(origin="https://cc.example"), remote) is True
