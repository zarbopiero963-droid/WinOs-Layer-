"""WebSocket event bus endpoint."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from windows_os_api.core.events.bus import get_event_bus
from windows_os_api.core.runtime.config import get_settings
from windows_os_api.core.security.auth import build_auth_context

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/events")
async def events_ws(websocket: WebSocket) -> None:
    settings = get_settings()
    # Same principal resolution as REST (N011) — query param api_key for WS.
    api_key = websocket.query_params.get("api_key")
    try:
        build_auth_context(api_key, settings)
    except HTTPException:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    bus = get_event_bus()
    try:
        async for event in bus.subscribe():
            await websocket.send_text(
                json.dumps({"id": event.id, "type": event.type, "payload": event.payload, "ts": event.ts})
            )
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        return
