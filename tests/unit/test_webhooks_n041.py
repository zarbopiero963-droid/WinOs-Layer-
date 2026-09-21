"""N041 / H63-N041 — Authenticated webhooks, idempotent delivery, egress limits.

Unit-level on box. Full installed W/L H63-N041 is MANUAL_ONLY (#21).
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from windows_os_api.core.events.bus import Event, get_event_bus, reset_event_bus
from windows_os_api.core.events.schema import ALLOWED_EVENT_TYPES
from windows_os_api.core.events.webhooks import (
    HEADER_EVENT_ID,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    WEBHOOK_EVENT_TYPES,
    UrllibTransport,
    WebhookDeliveryError,
    WebhookDestination,
    WebhookDispatcher,
    get_webhook_dispatcher,
    reset_webhook_dispatcher,
    sign_payload,
    verify_signature,
    validate_webhook_url,
)


@pytest.fixture(autouse=True)
def _clean_webhooks():
    reset_webhook_dispatcher()
    reset_event_bus()
    yield
    reset_webhook_dispatcher()
    reset_event_bus()


class RecordingTransport:
    """Injectable HTTP client: records POSTs; optional fail-then-succeed."""

    def __init__(self, *, fail_times: int = 0, side_effect_store: dict | None = None):
        self.calls: list[dict[str, Any]] = []
        self._fail_remaining = fail_times
        self.side_effect_store = side_effect_store if side_effect_store is not None else {}
        self.expected_secret: bytes = b""

    def post(
        self,
        url: str,
        *,
        body: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, bytes]:
        self.calls.append(
            {"url": url, "body": body, "headers": dict(headers), "timeout": timeout}
        )
        event_id = headers.get(HEADER_EVENT_ID, "")
        secret = self.expected_secret
        ts = headers.get(HEADER_TIMESTAMP, "")
        sig = headers.get(HEADER_SIGNATURE, "")
        if secret and not verify_signature(
            body, event_id=event_id, timestamp=ts, signature=sig, secret=secret
        ):
            return 401, b"bad signature"
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise TimeoutError("simulated timeout")
        bucket = self.side_effect_store.setdefault(event_id, {"applied": 0})
        if bucket["applied"] == 0:
            bucket["applied"] = 1
            bucket["payload"] = body
        return 200, b"ok"


def test_sign_and_verify_roundtrip():
    secret = b"test-webhook-secret-n041"
    body = b'{"type":"workflow.step.executed","id":"evt-1"}'
    event_id = "evt-1"
    ts = str(int(time.time()))
    sig = sign_payload(body, event_id=event_id, timestamp=ts, secret=secret)
    assert sig.startswith("sha256=")
    assert verify_signature(
        body, event_id=event_id, timestamp=ts, signature=sig, secret=secret
    )


def test_tampered_body_fails_verify():
    secret = b"test-webhook-secret-n041"
    body = b'{"ok":true}'
    event_id = "evt-2"
    ts = "1710000000"
    sig = sign_payload(body, event_id=event_id, timestamp=ts, secret=secret)
    assert not verify_signature(
        b'{"ok":false}',
        event_id=event_id,
        timestamp=ts,
        signature=sig,
        secret=secret,
    )


def test_stable_event_id_in_headers():
    transport = RecordingTransport()
    transport.expected_secret = b"sec"
    disp = WebhookDispatcher(transport=transport)
    dest = disp.register(
        WebhookDestination(
            id="d1",
            url="https://hooks.example.com/winos",
            secret=b"sec",
            event_types=frozenset({"workflow.step.executed"}),
        ),
        skip_dns=True,
    )
    assert dest.id == "d1"
    event = Event(
        type="workflow.step.executed",
        payload={"step": 1},
        id="stable-event-abc",
    )
    result = disp.deliver(event, destination_id="d1")
    assert result.ok
    assert len(transport.calls) == 1
    hdrs = transport.calls[0]["headers"]
    assert hdrs[HEADER_EVENT_ID] == "stable-event-abc"
    assert HEADER_SIGNATURE in hdrs
    assert HEADER_TIMESTAMP in hdrs
    assert verify_signature(
        transport.calls[0]["body"],
        event_id="stable-event-abc",
        timestamp=hdrs[HEADER_TIMESTAMP],
        signature=hdrs[HEADER_SIGNATURE],
        secret=b"sec",
    )


def test_retry_after_timeout_does_not_double_apply_side_effect():
    store: dict = {}
    transport = RecordingTransport(fail_times=1, side_effect_store=store)
    transport.expected_secret = b"sec"
    disp = WebhookDispatcher(
        transport=transport,
        max_attempts=3,
        backoff_seconds=(0.0, 0.0, 0.0),
    )
    disp.register(
        WebhookDestination(
            id="d1",
            url="https://hooks.example.com/hook",
            secret=b"sec",
            event_types=frozenset({"workflow.step.executed"}),
        ),
        skip_dns=True,
    )
    event = Event(type="workflow.step.executed", payload={"n": 1}, id="idem-1")
    r = disp.deliver(event, destination_id="d1")
    assert r.ok
    assert r.attempts == 2
    assert len(transport.calls) == 2
    assert store["idem-1"]["applied"] == 1

    r2 = disp.deliver(event, destination_id="d1")
    assert r2.ok
    assert r2.skipped_idempotent is True
    assert len(transport.calls) == 2
    assert store["idem-1"]["applied"] == 1


def test_receiver_rejects_bad_signature_no_side_effect():
    store: dict = {}
    transport = RecordingTransport(side_effect_store=store)
    transport.expected_secret = b"correct-secret"
    disp = WebhookDispatcher(transport=transport, max_attempts=1)
    disp.register(
        WebhookDestination(
            id="d1",
            url="https://hooks.example.com/hook",
            secret=b"wrong-secret",
            event_types=frozenset({"workflow.step.executed"}),
        ),
        skip_dns=True,
    )
    event = Event(type="workflow.step.executed", payload={}, id="bad-sig-1")
    r = disp.deliver(event, destination_id="d1")
    assert not r.ok
    assert store.get("bad-sig-1", {}).get("applied", 0) == 0


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.example.com/hook",
        "https://127.0.0.1/hook",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.1/hook",
        "https://192.168.1.1/hook",
        "https://[::1]/hook",
        "https://metadata.google.internal/",
        "ftp://hooks.example.com/x",
        "https://user:pass@hooks.example.com/x",
    ],
)
def test_forbidden_urls_denied_at_register(url):
    disp = WebhookDispatcher(transport=RecordingTransport())
    with pytest.raises(WebhookDeliveryError) as ei:
        disp.register(
            WebhookDestination(
                id="bad",
                url=url,
                secret=b"sec",
                event_types=frozenset({"workflow.step.executed"}),
            ),
            skip_dns=True,
        )
    assert ei.value.code in {
        "SCHEME_DENIED",
        "IP_DENIED",
        "HOST_DENIED",
        "URL_CREDENTIALS",
        "URL_INVALID",
    }


def test_unregistered_host_cannot_be_delivered_to():
    transport = RecordingTransport()
    disp = WebhookDispatcher(transport=transport)
    disp.register(
        WebhookDestination(
            id="ok",
            url="https://hooks.example.com/a",
            secret=b"sec",
            event_types=frozenset({"workflow.step.executed"}),
        ),
        skip_dns=True,
    )
    with pytest.raises(WebhookDeliveryError) as ei:
        disp.deliver(
            Event(type="workflow.step.executed", payload={}, id="e1"),
            destination_id="missing",
        )
    assert ei.value.code == "DEST_NOT_FOUND"
    assert transport.calls == []


def test_validate_webhook_url_allows_explicit_https_public_host():
    out = validate_webhook_url("https://hooks.example.com/path", resolve_dns=False)
    assert out.startswith("https://hooks.example.com")


def test_loopback_http_only_via_explicit_test_flag():
    with pytest.raises(WebhookDeliveryError):
        validate_webhook_url("http://127.0.0.1:9999/hook", resolve_dns=False)
    out = validate_webhook_url(
        "http://127.0.0.1:9999/hook",
        resolve_dns=False,
        allow_loopback_http=True,
    )
    assert "127.0.0.1" in out


def test_webhook_event_types_are_subset_of_allowed():
    assert WEBHOOK_EVENT_TYPES <= ALLOWED_EVENT_TYPES
    assert "workflow.step.executed" in WEBHOOK_EVENT_TYPES
    assert "system.crash" in WEBHOOK_EVENT_TYPES
    assert "system.crash" in ALLOWED_EVENT_TYPES


def test_non_webhook_type_not_delivered():
    transport = RecordingTransport()
    transport.expected_secret = b"sec"
    disp = WebhookDispatcher(transport=transport)
    disp.register(
        WebhookDestination(
            id="d1",
            url="https://hooks.example.com/a",
            secret=b"sec",
            event_types=frozenset(WEBHOOK_EVENT_TYPES),
        ),
        skip_dns=True,
    )
    event = Event(type="system.probe", payload={}, id="probe-1")
    r = disp.deliver(event, destination_id="d1")
    assert r.skipped_type is True
    assert transport.calls == []


def test_bus_publish_triggers_webhook_for_matching_type():
    transport = RecordingTransport()
    transport.expected_secret = b"sec"
    disp = get_webhook_dispatcher()
    disp.set_transport(transport)
    disp.register(
        WebhookDestination(
            id="d1",
            url="https://hooks.example.com/a",
            secret=b"sec",
            event_types=frozenset({"system.crash"}),
        ),
        skip_dns=True,
    )
    bus = get_event_bus()
    accepted = bus.publish_sync(
        "system.crash",
        {"component": "agent", "message": "segfault"},
        provenance="system",
    )
    assert accepted is not None
    assert accepted.type == "system.crash"
    assert len(transport.calls) >= 1
    assert transport.calls[0]["headers"][HEADER_EVENT_ID] == accepted.id


def test_dns_trick_private_resolution_denied(monkeypatch):
    from windows_os_api.core.events import webhooks as wh

    def fake_resolve(hostname: str) -> list[str]:
        return ["10.0.0.55"]

    monkeypatch.setattr(wh, "_resolve_host_ips", fake_resolve)
    with pytest.raises(WebhookDeliveryError) as ei:
        validate_webhook_url("https://evil-ssrf.example/hook", resolve_dns=True)
    assert ei.value.code == "DNS_IP_DENIED"


def test_rest_register_list_delete_destination(tmp_sandbox, monkeypatch):
    monkeypatch.setenv("WINOS_ADMIN_API_KEYS", '["admin-key-n041xxxxxxxx"]')
    monkeypatch.setenv("WINOS_API_KEYS", '["user-key-n041xxxxxxxxx"]')
    monkeypatch.setenv("WINOS_REMOTE_ACCESS_ENABLED", "false")
    from windows_os_api.core.runtime.config import get_settings
    from windows_os_api.core.runtime.app import create_app
    from windows_os_api.core.security.auth import reset_auth_registry
    from starlette.testclient import TestClient

    get_settings.cache_clear()
    reset_auth_registry()
    reset_webhook_dispatcher()
    app = create_app(get_settings())
    with TestClient(app) as client:
        r = client.post(
            "/v1/webhooks/destinations",
            headers={"X-API-Key": "user-key-n041xxxxxxxxx"},
            json={
                "id": "hook1",
                "url": "https://hooks.example.com/h",
                "secret": "super-secret-value",
                "event_types": ["workflow.step.executed"],
            },
        )
        assert r.status_code == 403

        r = client.post(
            "/v1/webhooks/destinations",
            headers={"X-API-Key": "admin-key-n041xxxxxxxx"},
            json={
                "id": "hook1",
                "url": "https://hooks.example.com/h",
                "secret": "super-secret-value",
                "event_types": ["workflow.step.executed"],
                "skip_dns": True,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["id"] == "hook1"
        assert body.get("secret") in (None, "", "[REDACTED]") or "secret" not in body
        assert body["url"].startswith("https://hooks.example.com")

        r = client.get(
            "/v1/webhooks/destinations",
            headers={"X-API-Key": "admin-key-n041xxxxxxxx"},
        )
        assert r.status_code == 200
        items = r.json()["destinations"]
        assert any(d["id"] == "hook1" for d in items)
        for d in items:
            assert d.get("secret") in (None, "", "[REDACTED]") or "secret" not in d

        r = client.delete(
            "/v1/webhooks/destinations/hook1",
            headers={"X-API-Key": "admin-key-n041xxxxxxxx"},
        )
        assert r.status_code == 200
        r = client.get(
            "/v1/webhooks/destinations",
            headers={"X-API-Key": "admin-key-n041xxxxxxxx"},
        )
        assert all(d["id"] != "hook1" for d in r.json()["destinations"])

    reset_auth_registry()
    get_settings.cache_clear()


def test_default_dispatcher_uses_urllib_transport():
    """Audit H63-N041: production singleton must not stay on NullTransport."""
    from windows_os_api.core.events.webhooks import (
        UrllibTransport,
        get_webhook_dispatcher,
        reset_webhook_dispatcher,
    )

    reset_webhook_dispatcher()
    disp = get_webhook_dispatcher()
    assert isinstance(disp.transport, UrllibTransport)
