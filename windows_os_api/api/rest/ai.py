"""AI provider settings + connectivity test (admin)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from windows_os_api.api.rest.deps import audit
from windows_os_api.apps.ai.provider import get_ai_client, sync_llm_bridge
from windows_os_api.apps.ai.settings_store import get_ai_settings, update_ai_settings
from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission

router = APIRouter(tags=["ai"])

ProviderLiteral = Literal["local", "openai", "anthropic", "openrouter"]


class AISettingsUpdate(BaseModel):
    provider: ProviderLiteral | None = None
    api_key: str | None = Field(
        default=None,
        description="New API key; empty string clears. Omit to leave unchanged.",
    )
    model: str | None = None
    base_url: str | None = None


class AITestBody(BaseModel):
    spend: bool = Field(
        default=False,
        description="If true, perform a lightweight remote chat call (may incur cost).",
    )


@router.get("/ai/settings")
def get_settings_route(auth: AuthContext = Depends(require_permission(Permission.ADMIN))):
    s = get_ai_settings()
    audit("ai.settings.get", auth, resource="ai", detail=s.audit_detail())
    return s.public_dict()


@router.put("/ai/settings")
def put_settings_route(
    body: AISettingsUpdate,
    auth: AuthContext = Depends(require_permission(Permission.ADMIN)),
):
    try:
        updated = update_ai_settings(
            provider=body.provider,
            api_key=body.api_key,
            model=body.model,
            base_url=body.base_url,
            persist=True,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    sync_llm_bridge()
    audit("ai.settings.put", auth, resource="ai", detail=updated.audit_detail())
    return updated.public_dict()


@router.post("/ai/test")
def test_ai_route(
    body: AITestBody | None = None,
    auth: AuthContext = Depends(require_permission(Permission.ADMIN)),
):
    body = body or AITestBody()
    client = get_ai_client(force_new=True)
    result = client.test_connectivity(spend=body.spend)
    # Strip anything that could leak secrets from audit
    audit(
        "ai.test",
        auth,
        resource="ai",
        detail={
            "ok": result.get("ok"),
            "provider": result.get("provider"),
            "api_key_set": result.get("api_key_set"),
            "skipped_network": result.get("skipped_network"),
            "spent": result.get("spent"),
        },
        outcome="success" if result.get("ok") else "failure",
    )
    return result
