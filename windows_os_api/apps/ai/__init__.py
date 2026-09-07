"""Optional remote AI providers (OpenAI / Anthropic / OpenRouter) + local fallback."""
from __future__ import annotations

from windows_os_api.apps.ai.provider import (
    AIProviderClient,
    get_ai_client,
    reset_ai_client,
)
from windows_os_api.apps.ai.settings_store import (
    AIRuntimeSettings,
    get_ai_settings,
    mask_api_key,
    reset_ai_settings,
    update_ai_settings,
)

__all__ = [
    "AIProviderClient",
    "AIRuntimeSettings",
    "get_ai_client",
    "get_ai_settings",
    "mask_api_key",
    "reset_ai_client",
    "reset_ai_settings",
    "update_ai_settings",
]
