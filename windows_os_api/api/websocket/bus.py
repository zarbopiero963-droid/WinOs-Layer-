"""WebSocket event bus endpoint."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from windows_os_api.core.events.bus import get_event_bus
from windows_os_api.core.runtime.config import get_settings

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/events")
async def events_ws(websocket: WebSocket) -> None:
    settings = get_settings()
    # Auth via query param api_key for WS
    api_key = websocket.query_params.get("api_key")
    if settings.require_auth:
        valid = (api_key in settings.api_keys) or (api_key in settings.admin_api_keys)
        if not valid:
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
