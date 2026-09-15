"""API schema generation for apps / adapters (N019)."""
from __future__ import annotations
from typing import Any

from windows_os_api.apps.adapters.engine import get_adapter, generate_adapter_openapi
from windows_os_api.apps.schema.openapi_export import (
    OpenAPISchemaRejected,
    assemble_openapi_document,
    finalize_openapi_export,
)


class AdapterOpenAPINotFound(LookupError):
    """No adapter registered for app_id — OpenAPI read must not create one (N017/N019)."""


def app_openapi(app_id: str) -> dict[str, Any]:
    """Return the per-app Virtual API document.

    Fail-closed: never implicitly ``create_adapter`` on OpenAPI read (N017).
    Regenerates from current verification state each call.
    """
    adapter = get_adapter(app_id)
    if adapter is None:
        raise AdapterOpenAPINotFound(
            f"adapter not found for app_id={app_id!r}; create explicitly before OpenAPI export"
        )
    # Il documento e' dinamico: una verifica successiva puo' aggiungere una
    # capability, mentre un fallimento successivo deve rimuoverla subito.
    adapter.openapi = generate_adapter_openapi(adapter)
    return adapter.openapi


def platform_capabilities_schema() -> dict[str, Any]:
    doc = assemble_openapi_document(
        title="Windows OS API Layer",
        paths={
            "/v1/health": {
                "get": {
                    "summary": "Health",
                    "operationId": "platform__health_get",
                    "responses": {"200": {"description": "Health"}},
                    "x-risk": "low",
                    "x-scopes": ["system.read"],
                    "x-permissions": ["system.read"],
                    "x-auth": {"required": True, "schemes": ["ApiKeyAuth"]},
                    "x-version": "1.0.0",
                    "x-schema-version": "n019-1",
                    "x-crud": "read",
                }
            },
            "/v1/system": {
                "get": {
                    "summary": "System info",
                    "operationId": "platform__system_get",
                    "responses": {"200": {"description": "System"}},
                    "x-risk": "low",
                    "x-scopes": ["system.read"],
                    "x-permissions": ["system.read"],
                    "x-auth": {"required": True, "schemes": ["ApiKeyAuth"]},
                    "x-version": "1.0.0",
                    "x-schema-version": "n019-1",
                    "x-crud": "read",
                }
            },
            "/v1/capabilities": {
                "get": {
                    "summary": "Capabilities",
                    "operationId": "platform__capabilities_get",
                    "responses": {"200": {"description": "Capabilities"}},
                    "x-risk": "low",
                    "x-scopes": ["system.read"],
                    "x-permissions": ["system.read"],
                    "x-auth": {"required": True, "schemes": ["ApiKeyAuth"]},
                    "x-version": "1.0.0",
                    "x-schema-version": "n019-1",
                    "x-crud": "read",
                }
            },
        },
        description="Platform capability surface (not Virtual API).",
    )
    try:
        return finalize_openapi_export(doc)
    except OpenAPISchemaRejected:
        # Platform stub must still be valid under N019 rules.
        raise


def app_sdk(app_id: str) -> str:
    """Return deterministic Python SDK source for a per-app Virtual API (N025).

    Fail-closed: reuses ``app_openapi`` (never implicit create_adapter).
    """
    from windows_os_api.apps.schema.sdk_export import generate_python_sdk

    doc = app_openapi(app_id)
    return generate_python_sdk(doc)
