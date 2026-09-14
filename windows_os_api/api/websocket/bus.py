"""WebSocket event bus endpoint."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from windows_os_api.core.events.bus import get_event_bus
from windows_os_api.core.runtime.config import get_settings
from windows_os_api.core.security.auth import build_auth_context, get_auth_registry

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/events")
async def events_ws(websocket: WebSocket) -> None:
    settings = get_settings()
    # Same principal resolution as REST (N011) — query param api_key for WS.
    api_key = websocket.query_params.get("api_key")
    try:
        ctx = build_auth_context(api_key, settings)
    except HTTPException:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    # N012: remember session; drop if revoked before fan-out (side-effect-adjacent).
    session_id = ctx.session_id
    key_fp = ctx.key_fingerprint
    bus = get_event_bus()
    registry = get_auth_registry()
    try:
        async for event in bus.subscribe():
            if key_fp and registry.is_revoked_fp(key_fp):
                await websocket.close(code=4401)
                return
            if session_id and registry.is_session_revoked(session_id):
                await websocket.close(code=4401)
                return
            # Subject/app isolation for bus payloads is N040; here we only enforce auth liveness.
            await websocket.send_text(
                json.dumps({"id": event.id, "type": event.type, "payload": event.payload, "ts": event.ts})
            )
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        return
