"""N041 — Authenticated webhook delivery with egress limits and idempotent retry.

Destinations must be explicitly registered. Delivery signs the body with
HMAC-SHA256 and tracks ``(destination_id, event_id)`` so retries / duplicates
do not re-apply side effects. Egress is fail-closed: https only (optional
``http://127.0.0.1`` for unit-test receivers), no private/link-local/metadata
IPs, no unregistered hosts.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

# Headers presented to receivers (H63-N041).
HEADER_SIGNATURE = "X-WinOS-Signature"
HEADER_EVENT_ID = "X-WinOS-Event-Id"
HEADER_TIMESTAMP = "X-WinOS-Timestamp"

# Subset of bus allowlist eligible for webhook fan-out (workflow / capability / crash).
WEBHOOK_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "workflow.step.proposed",
        "workflow.step.executed",
        "agent.goal.denied",  # capability denial signal
        "agent.goal.executed",
        "system.crash",
    }
)

_BLOCKED_LITERAL_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
    }
)

_DEFAULT_BACKOFF = (0.05, 0.1, 0.2)
_DEFAULT_TIMEOUT_S = 5.0
_DEFAULT_MAX_ATTEMPTS = 3


class WebhookDeliveryError(Exception):
    def __init__(self, message: str, *, code: str = "EGRESS_DENIED") -> None:
        super().__init__(message)
        self.code = code


class HttpTransport(Protocol):
    def post(
        self,
        url: str,
        *,
        body: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, bytes]: ...


@dataclass
class WebhookDestination:
    id: str
    url: str
    secret: bytes
    event_types: frozenset[str] = field(default_factory=lambda: frozenset(WEBHOOK_EVENT_TYPES))
    allow_loopback_http: bool = False


@dataclass
class DeliveryResult:
    ok: bool
    attempts: int = 0
    skipped_idempotent: bool = False
    skipped_type: bool = False
    status_code: int | None = None
    error: str | None = None
    code: str | None = None


def _host_is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def is_blocked_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return True
    if addr.is_multicast or addr.is_reserved or addr.is_unspecified:
        return True
    if str(addr) in ("169.254.169.254", "fd00:ec2::254"):
        return True
    return False


def _resolve_host_ips(hostname: str) -> list[str]:
    ips: list[str] = []
    try:
        for family, _t, _p, _c, sockaddr in socket.getaddrinfo(
            hostname, None, type=socket.SOCK_STREAM
        ):
            if family == socket.AF_INET:
                ips.append(sockaddr[0])
            elif family == socket.AF_INET6:
                ips.append(sockaddr[0])
    except OSError:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for ip in ips:
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def validate_webhook_url(
    url: str,
    *,
    resolve_dns: bool = True,
    allow_loopback_http: bool = False,
) -> str:
    """Validate destination URL. Fail-closed on SSRF / scheme / credentials."""
    if url is None or not str(url).strip():
        raise WebhookDeliveryError("webhook URL required", code="URL_MISSING")
    raw = str(url).strip()
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower().rstrip(".")

    if parsed.username is not None or parsed.password is not None:
        raise WebhookDeliveryError("credentials in URL are forbidden", code="URL_CREDENTIALS")
    if not host:
        raise WebhookDeliveryError("webhook URL missing hostname", code="HOST_MISSING")

    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    if allow_loopback_http and scheme == "http" and host in loopback_hosts:
        # Explicit unit-test receiver path only.
        path = parsed.path or ""
        port = f":{parsed.port}" if parsed.port else ""
        return f"http://{host}{port}{path}"

    if scheme != "https":
        raise WebhookDeliveryError(
            f"webhook egress requires https (got {scheme!r})",
            code="SCHEME_DENIED",
        )
    if host in _BLOCKED_LITERAL_HOSTS:
        raise WebhookDeliveryError(f"host {host!r} is blocked", code="HOST_DENIED")

    if _host_is_ip_literal(host):
        if is_blocked_ip(host):
            raise WebhookDeliveryError(
                "webhook egress to private/link-local/metadata IP is forbidden",
                code="IP_DENIED",
            )
    elif resolve_dns:
        ips = _resolve_host_ips(host)
        if not ips:
            raise WebhookDeliveryError(
                f"DNS resolution failed for {host!r}",
                code="DNS_FAILED",
            )
        for ip in ips:
            if is_blocked_ip(ip):
                raise WebhookDeliveryError(
                    "webhook DNS resolved to private/link-local/metadata IP",
                    code="DNS_IP_DENIED",
                )

    path = parsed.path or ""
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    normalised = f"https://{host}{port}{path}"
    if parsed.query:
        normalised = f"{normalised}?{parsed.query}"
    return normalised


def _signing_message(body: bytes, *, event_id: str, timestamp: str) -> bytes:
    # Stable canonical: timestamp + "." + event_id + "." + body
    return timestamp.encode("utf-8") + b"." + event_id.encode("utf-8") + b"." + body


def sign_payload(
    body: bytes,
    *,
    event_id: str,
    timestamp: str,
    secret: bytes,
) -> str:
    digest = hmac.new(
        secret,
        _signing_message(body, event_id=event_id, timestamp=timestamp),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def verify_signature(
    body: bytes,
    *,
    event_id: str,
    timestamp: str,
    signature: str,
    secret: bytes,
) -> bool:
    if not signature or not event_id or not timestamp:
        return False
    expected = sign_payload(body, event_id=event_id, timestamp=timestamp, secret=secret)
    return hmac.compare_digest(expected, signature)


class NullTransport:
    """Default transport — refuses real network (tests inject a fake)."""

    def post(
        self,
        url: str,
        *,
        body: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, bytes]:
        raise WebhookDeliveryError(
            "no HTTP transport configured for webhook delivery",
            code="TRANSPORT_MISSING",
        )


@dataclass
class WebhookDispatcher:
    transport: HttpTransport = field(default_factory=NullTransport)
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS
    backoff_seconds: tuple[float, ...] = _DEFAULT_BACKOFF
    timeout_s: float = _DEFAULT_TIMEOUT_S

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._destinations: dict[str, WebhookDestination] = {}
        # Idempotency: (destination_id, event_id) already delivered successfully.
        self._delivered: set[tuple[str, str]] = set()

    def set_transport(self, transport: HttpTransport) -> None:
        self.transport = transport

    def register(
        self,
        dest: WebhookDestination,
        *,
        skip_dns: bool = False,
    ) -> WebhookDestination:
        if not dest.id or not str(dest.id).strip():
            raise WebhookDeliveryError("destination id required", code="DEST_INVALID")
        if not dest.secret:
            raise WebhookDeliveryError("destination secret required", code="SECRET_MISSING")
        # Only webhook-eligible types may be subscribed.
        types = frozenset(dest.event_types) if dest.event_types else frozenset(WEBHOOK_EVENT_TYPES)
        unknown = types - WEBHOOK_EVENT_TYPES
        if unknown:
            raise WebhookDeliveryError(
                f"event types not webhook-eligible: {sorted(unknown)}",
                code="TYPE_DENIED",
            )
        normalised = validate_webhook_url(
            dest.url,
            resolve_dns=not skip_dns,
            allow_loopback_http=dest.allow_loopback_http,
        )
        stored = WebhookDestination(
            id=str(dest.id).strip(),
            url=normalised,
            secret=dest.secret if isinstance(dest.secret, bytes) else bytes(dest.secret),
            event_types=types,
            allow_loopback_http=dest.allow_loopback_http,
        )
        with self._lock:
            self._destinations[stored.id] = stored
        return stored

    def unregister(self, destination_id: str) -> bool:
        with self._lock:
            return self._destinations.pop(destination_id, None) is not None

    def list_destinations(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": d.id,
                    "url": d.url,
                    "event_types": sorted(d.event_types),
                    "allow_loopback_http": d.allow_loopback_http,
                }
                for d in self._destinations.values()
            ]

    def _event_body(self, event: Any) -> bytes:
        payload = {
            "id": getattr(event, "id", ""),
            "type": getattr(event, "type", ""),
            "ts": getattr(event, "ts", time.time()),
            "provenance": getattr(event, "provenance", "internal"),
            "payload": getattr(event, "payload", {}) or {},
        }
        return json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")

    def deliver(self, event: Any, *, destination_id: str) -> DeliveryResult:
        with self._lock:
            dest = self._destinations.get(destination_id)
        if dest is None:
            raise WebhookDeliveryError(
                f"destination {destination_id!r} not registered",
                code="DEST_NOT_FOUND",
            )
        event_type = getattr(event, "type", "")
        event_id = str(getattr(event, "id", "") or "")
        if event_type not in WEBHOOK_EVENT_TYPES or event_type not in dest.event_types:
            return DeliveryResult(ok=True, skipped_type=True, attempts=0)

        key = (dest.id, event_id)
        with self._lock:
            if key in self._delivered:
                return DeliveryResult(ok=True, skipped_idempotent=True, attempts=0)

        # Re-validate URL at delivery time (fail-closed; skip DNS for already-registered).
        try:
            validate_webhook_url(
                dest.url,
                resolve_dns=False,
                allow_loopback_http=dest.allow_loopback_http,
            )
        except WebhookDeliveryError as exc:
            return DeliveryResult(ok=False, attempts=0, error=str(exc), code=exc.code)

        body = self._event_body(event)
        last_err: str | None = None
        last_code: str | None = None
        last_status: int | None = None
        attempts = 0
        for attempt in range(1, self.max_attempts + 1):
            attempts = attempt
            ts = str(int(time.time()))
            sig = sign_payload(body, event_id=event_id, timestamp=ts, secret=dest.secret)
            headers = {
                HEADER_SIGNATURE: sig,
                HEADER_EVENT_ID: event_id,
                HEADER_TIMESTAMP: ts,
                "Content-Type": "application/json",
            }
            try:
                status, _resp = self.transport.post(
                    dest.url, body=body, headers=headers, timeout=self.timeout_s
                )
                last_status = status
                if 200 <= status < 300:
                    with self._lock:
                        self._delivered.add(key)
                    return DeliveryResult(
                        ok=True, attempts=attempts, status_code=status
                    )
                # 4xx (except 408/429) — do not retry (auth / client error).
                if status in (408, 429) or status >= 500:
                    last_err = f"HTTP {status}"
                    last_code = "HTTP_RETRYABLE"
                else:
                    last_err = f"HTTP {status}"
                    last_code = "HTTP_CLIENT"
                    break
            except WebhookDeliveryError as exc:
                last_err = str(exc)
                last_code = exc.code
                break
            except Exception as exc:  # timeout / network
                last_err = str(exc)
                last_code = "TRANSPORT_ERROR"
            if attempt < self.max_attempts:
                delay = self.backoff_seconds[
                    min(attempt - 1, len(self.backoff_seconds) - 1)
                ]
                if delay > 0:
                    time.sleep(delay)

        return DeliveryResult(
            ok=False,
            attempts=attempts,
            status_code=last_status,
            error=last_err,
            code=last_code,
        )

    def fan_out(self, event: Any) -> list[DeliveryResult]:
        """Deliver to all registered destinations that match the event type."""
        event_type = getattr(event, "type", "")
        if event_type not in WEBHOOK_EVENT_TYPES:
            return []
        with self._lock:
            dest_ids = [
                d.id
                for d in self._destinations.values()
                if event_type in d.event_types
            ]
        results: list[DeliveryResult] = []
        for did in dest_ids:
            try:
                results.append(self.deliver(event, destination_id=did))
            except WebhookDeliveryError as exc:
                results.append(
                    DeliveryResult(ok=False, attempts=0, error=str(exc), code=exc.code)
                )
        return results


_dispatcher: WebhookDispatcher | None = None
_dispatcher_guard = threading.Lock()


def get_webhook_dispatcher() -> WebhookDispatcher:
    global _dispatcher
    with _dispatcher_guard:
        if _dispatcher is None:
            _dispatcher = WebhookDispatcher()
        return _dispatcher


def reset_webhook_dispatcher() -> None:
    global _dispatcher
    with _dispatcher_guard:
        _dispatcher = None
