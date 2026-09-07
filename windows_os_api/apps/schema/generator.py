"""API schema generation for apps / adapters."""
from __future__ import annotations
from typing import Any
from windows_os_api.apps.adapters.engine import get_adapter, create_adapter, generate_adapter_openapi

def app_openapi(app_id: str, hwnd: int = 1001) -> dict[str, Any]:
    adapter = get_adapter(app_id) or create_adapter(app_id, hwnd)
    return generate_adapter_openapi(adapter)

def platform_capabilities_schema() -> dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Windows OS API Layer", "version": "1.0.0"},
        "paths": {
            "/v1/health": {"get": {"summary": "Health"}},
            "/v1/system": {"get": {"summary": "System info"}},
            "/v1/capabilities": {"get": {"summary": "Capabilities"}},
        },
    }
