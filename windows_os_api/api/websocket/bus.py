"""WebSocket event bus endpoint (N040).

Auth (browser-safe — **no secret in URL**):
1. ``?api_key=`` query is **always rejected** (close 4401).
2. Preferred non-browser / TestClient: ``X-API-Key`` handshake header.
3. Browser-capable: ``Sec-WebSocket-Protocol: winos.apikey.<base64url(key)>``
   (key is base64url-encoded so it fits the subprotocol token charset).
4. Fallback: after accept, first JSON message
   ``{"type":"auth","api_key":"..."}`` within ``WS_AUTH_TIMEOUT_S``.

Origin / remote guard (aligned with N013 HTTP policy):
- ``remote_access_enabled``: Origin required and must be in
  ``cors_allowed_origins`` (or derived localhost allowlist if empty).
- Localhost-only: non-loopback peer denied; if Origin is present it must
  be a local/derived origin (hostile Origin → 4403). Missing Origin on
  loopback peers is allowed (native clients / TestClient).

Fan-out isolation (fail-closed):
- ADMIN sees all events.
- Non-admin with empty ``app_scopes``: only events **without** app binding
  (``application_id`` / ``app_id``) and without foreign owner/subject.
- App-bound events require the app id in the principal's scopes.
- Events with ``owner_subject`` / ``subject`` / ``user_id`` must match the
  principal (``subject`` or ``user_id``).

RBAC: ``Permission.SYSTEM_READ`` required to subscribe.
Caps: auth timeout, idle timeout, per-connection send flood window.
Revocation mid-stream (N012) unchanged. Bus capacity/closed (N039) → 1013/1012.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from windows_os_api.core.events.bus import (
    BusAtCapacityError,
    BusClosedError,
    Event,
    get_event_bus,
)
from windows_os_api.core.permissions.model import Permission, Role
from windows_os_api.core.runtime.config import Settings, get_settings
from windows_os_api.core.security.auth import AuthContext, build_auth_context, get_auth_registry

router = APIRouter(tags=["websocket"])

# Tunables (tests may monkeypatch).
WS_AUTH_TIMEOUT_S = 5.0
WS_IDLE_TIMEOUT_S = 120.0
WS_MAX_SENDS_PER_WINDOW = 120
WS_SEND_WINDOW_S = 1.0

_SUBPROTOCOL_PREFIX = "winos.apikey."
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _b64url_encode(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _b64url_decode(token: str) -> str | None:
    try:
        pad = "=" * (-len(token) % 4)
        return base64.urlsafe_b64decode(token + pad).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def encode_ws_subprotocol_key(api_key: str) -> str:
    """Build ``Sec-WebSocket-Protocol`` value for a raw API key (tests/clients)."""
    return _SUBPROTOCOL_PREFIX + _b64url_encode(api_key)


def _derived_origins(settings: Settings) -> list[str]:
    origins = [f"http://{h}:{settings.port}" for h in settings.allowed_hosts]
    origins += [f"https://{h}:{settings.port}" for h in settings.allowed_hosts]
    origins += [f"http://{h}" for h in settings.allowed_hosts]
    origins += [f"https://{h}" for h in settings.allowed_hosts]
    return origins


def _origin_is_local(origin: str) -> bool:
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def ws_origin_allowed(websocket: WebSocket, settings: Settings) -> bool:
    """Return True if the WS Origin/peer policy permits this handshake."""
    peer = websocket.client.host if websocket.client else ""
    origin = websocket.headers.get("origin")
    derived = _derived_origins(settings)

    if settings.remote_access_enabled:
        allowed = list(settings.cors_allowed_origins) or derived
        return bool(origin) and origin in allowed

    # Localhost-only (remote disabled).
    if peer and peer not in _LOOPBACK and peer not in settings.allowed_hosts:
        return False
    if origin:
        if origin in derived or _origin_is_local(origin):
            return True
        return False
    return True


def _extract_key_from_subprotocols(header_value: str | None) -> tuple[str | None, str | None]:
    """Return (raw_api_key, subprotocol_to_echo) from Sec-WebSocket-Protocol."""
    if not header_value:
        return None, None
    for part in header_value.split(","):
        token = part.strip()
        if not token.startswith(_SUBPROTOCOL_PREFIX):
            continue
        encoded = token[len(_SUBPROTOCOL_PREFIX) :]
        raw = _b64url_decode(encoded)
        if raw:
            return raw, token
    return None, None


def _build_ctx(api_key: str, settings: Settings) -> AuthContext | None:
    try:
        ctx = build_auth_context(api_key, settings)
    except HTTPException:
        return None
    if not ctx.check(Permission.SYSTEM_READ):
        return None
    return ctx


async def _authenticate_ws(
    websocket: WebSocket, settings: Settings
) -> AuthContext | None:
    """Authenticate; accept the socket on success. Return None if closed/denied."""
    # Secret-in-URL is always denied (H63-N040) — even if a header is also present.
    if "api_key" in websocket.query_params:
        await websocket.close(code=4401)
        return None

    header_key = websocket.headers.get("x-api-key")
    proto_key, proto_echo = _extract_key_from_subprotocols(
        websocket.headers.get("sec-websocket-protocol")
    )

    if header_key or proto_key:
        ctx = _build_ctx(header_key or proto_key or "", settings)
        if ctx is None:
            # Distinguish missing SYSTEM_READ (403) vs bad key (401) when possible.
            try:
                build_auth_context(header_key or proto_key or "", settings)
                await websocket.close(code=4403)
            except HTTPException:
                await websocket.close(code=4401)
            return None
        if proto_echo and proto_key and not header_key:
            await websocket.accept(subprotocol=proto_echo)
        else:
            await websocket.accept()
        return ctx

    # First-message auth (browser-safe).
    await websocket.accept()
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=WS_AUTH_TIMEOUT_S)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await websocket.close(code=4401)
        return None

    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await websocket.close(code=4401)
        return None
    if not isinstance(msg, dict) or msg.get("type") != "auth":
        await websocket.close(code=4401)
        return None
    api_key = msg.get("api_key")
    if not isinstance(api_key, str) or not api_key:
        await websocket.close(code=4401)
        return None

    ctx = _build_ctx(api_key, settings)
    if ctx is None:
        try:
            build_auth_context(api_key, settings)
            await websocket.close(code=4403)
        except HTTPException:
            await websocket.close(code=4401)
        return None
    return ctx


def event_visible_to(ctx: AuthContext, event: Event) -> bool:
    """Server-side app/subject filter (fail-closed for scoped non-admin)."""
    if ctx.role == Role.ADMIN or Permission.ADMIN in ctx.permissions:
        return True

    payload: dict[str, Any] = event.payload if isinstance(event.payload, dict) else {}
    app_id = payload.get("application_id") or payload.get("app_id")
    if isinstance(app_id, str):
        app_id = app_id.strip() or None
    else:
        app_id = None

    owner = (
        payload.get("owner_subject")
        or payload.get("subject")
        or payload.get("user_id")
    )
    if isinstance(owner, str):
        owner = owner.strip() or None
    else:
        owner = None

    registry = get_auth_registry()
    scopes = (
        registry.get_app_scopes_fp(ctx.key_fingerprint)
        if ctx.key_fingerprint
        else ctx.app_scopes
    ) or frozenset()

    if app_id:
        # Fail-closed: empty scopes → no app-bound events.
        if app_id not in scopes:
            return False

    if owner:
        if owner not in (ctx.subject, ctx.user_id):
            return False

    return True


@router.websocket("/ws/events")
async def events_ws(websocket: WebSocket) -> None:
    settings = get_settings()

    if not ws_origin_allowed(websocket, settings):
        await websocket.close(code=4403)
        return

    ctx = await _authenticate_ws(websocket, settings)
    if ctx is None:
        return

    try:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "auth_ok",
                    "subject": ctx.subject,
                    "role": ctx.role.value if hasattr(ctx.role, "value") else str(ctx.role),
                }
            )
        )
    except (WebSocketDisconnect, RuntimeError):
        return

    session_id = ctx.session_id
    key_fp = ctx.key_fingerprint
    bus = get_event_bus()
    registry = get_auth_registry()
    send_timestamps: list[float] = []

    def _flood_exceeded() -> bool:
        now = time.monotonic()
        while send_timestamps and now - send_timestamps[0] > WS_SEND_WINDOW_S:
            send_timestamps.pop(0)
        return len(send_timestamps) >= WS_MAX_SENDS_PER_WINDOW

    try:
        aiter = bus.subscribe().__aiter__()
        while True:
            try:
                event = await asyncio.wait_for(aiter.__anext__(), timeout=WS_IDLE_TIMEOUT_S)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                await websocket.close(code=1001)
                return

            if key_fp and registry.is_revoked_fp(key_fp):
                await websocket.close(code=4401)
                return
            if session_id and registry.is_session_revoked(session_id):
                await websocket.close(code=4401)
                return
            if not event_visible_to(ctx, event):
                continue
            if _flood_exceeded():
                await websocket.close(code=1013)
                return
            await websocket.send_text(
                json.dumps(
                    {
                        "id": event.id,
                        "type": event.type,
                        "payload": event.payload,
                        "ts": event.ts,
                    }
                )
            )
            send_timestamps.append(time.monotonic())
    except BusClosedError:
        await websocket.close(code=1012)
        return
    except BusAtCapacityError:
        await websocket.close(code=1013)
        return
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        return
