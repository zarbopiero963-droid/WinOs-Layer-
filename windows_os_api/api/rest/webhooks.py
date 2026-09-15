"""N041 — REST management for authenticated webhook destinations."""
from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status

from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission
from windows_os_api.api.rest.deps import audit as audit_event
from windows_os_api.core.events.webhooks import (
    WEBHOOK_EVENT_TYPES,
    WebhookDeliveryError,
    WebhookDestination,
    get_webhook_dispatcher,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class DestinationCreate(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=2048)
    secret: str = Field(min_length=8, max_length=512)
    event_types: list[str] = Field(default_factory=list)
    allow_loopback_http: bool = False
    # Unit / CI only: skip DNS resolution at register (still scheme/IP checks).
    skip_dns: bool = False


@router.get("/destinations")
def list_destinations(
    auth: AuthContext = Depends(require_permission(Permission.ADMIN)),
):
    items = get_webhook_dispatcher().list_destinations()
    audit_event("webhook.destinations.list", auth, resource="webhooks")
    return {"destinations": items}


@router.post("/destinations", status_code=status.HTTP_201_CREATED)
def create_destination(
    body: DestinationCreate,
    auth: AuthContext = Depends(require_permission(Permission.ADMIN)),
):
    types = frozenset(body.event_types) if body.event_types else frozenset(WEBHOOK_EVENT_TYPES)
    disp = get_webhook_dispatcher()
    try:
        dest = disp.register(
            WebhookDestination(
                id=body.id.strip(),
                url=body.url.strip(),
                secret=body.secret.encode("utf-8"),
                event_types=types,
                allow_loopback_http=body.allow_loopback_http,
            ),
            skip_dns=body.skip_dns,
        )
    except WebhookDeliveryError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc
    audit_event(
        "webhook.destination.create",
        auth,
        resource=dest.id,
        detail={"url": dest.url, "event_types": sorted(dest.event_types)},
    )
    return {
        "id": dest.id,
        "url": dest.url,
        "event_types": sorted(dest.event_types),
        "allow_loopback_http": dest.allow_loopback_http,
    }


@router.delete("/destinations/{destination_id}")
def delete_destination(
    destination_id: str,
    auth: AuthContext = Depends(require_permission(Permission.ADMIN)),
):
    ok = get_webhook_dispatcher().unregister(destination_id)
    if not ok:
        raise HTTPException(status_code=404, detail="destination not found")
    audit_event("webhook.destination.delete", auth, resource=destination_id)
    return {"ok": True, "id": destination_id}
