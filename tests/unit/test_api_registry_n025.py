"""N025 — SDK/export from VERIFIED OpenAPI contracts (H63-N025)."""
from __future__ import annotations

import os
import time

import pytest

from windows_os_api.apps.adapters.engine import (
    create_adapter,
    get_adapter,
    reset_adapters,
    verify_and_record,
)
from windows_os_api.apps.api_registry.model import (
    ApiRegistry,
    ApiStatus,
    reset_api_registry,
)
from windows_os_api.apps.schema.generator import AdapterOpenAPINotFound, app_openapi, app_sdk
from windows_os_api.apps.schema.openapi_export import finalize_openapi_export
from windows_os_api.apps.schema.sdk_export import (
    SDK_SCHEMA_VERSION,
    canonical_sdk_bytes,
    generate_python_sdk,
    load_generated_sdk,
)
from windows_os_api.core.security.auth import get_auth_registry


@pytest.fixture(autouse=True)
def _clean():
    reset_adapters()
    reset_api_registry()
    yield
    reset_adapters()
    reset_api_registry()


def _register_verified(reg: ApiRegistry, **overrides):
    now = time.time()
    payload = {
        "name": "Probe Search",
        "method": "POST",
        "path": "/v1/apps/probe-app/actions/search",
        "description": "Search in probe app",
        "source": "virtual_adapter",
        "application_id": "probe-app",
        "capability": "probe-app.search",
        "status": "VERIFIED",
        "verification_id": "ver_n025_probe_1",
        "last_verified_at": now,
        "permissions": ["ui.read", "ui.control"],
        "authentication_required": True,
    }
    payload.update(overrides)
    return reg.register(payload)


def _sdk_http_client(test_client):
    """Adapt Starlette/FastAPI TestClient to the generated SDK ``client=`` hook.

    Generated code calls ``client.request(method, absolute_url, ...)``; TestClient
    expects a path. No fake endpoints — requests hit the real ASGI app routes.
    """

    class _Adapter:
        def __init__(self, tc):
            self._tc = tc

        def request(self, method, url, **kwargs):
            from urllib.parse import urlparse

            parsed = urlparse(str(url))
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            # Drop unsupported timeout kw if TestClient rejects it — still pass headers/content.
            kwargs.pop("timeout", None)
            return self._tc.request(method, path, **kwargs)

    return _Adapter(test_client)


def test_twin_sdk_regenerate_identical_bytes():
    reg = reset_api_registry()
    _register_verified(reg)
    from windows_os_api.api.rest.apis import _registry_openapi_document

    doc = _registry_openapi_document(visible_app_ids=None)
    b1 = canonical_sdk_bytes(doc)
    b2 = canonical_sdk_bytes(doc)
    assert b1 == b2
    text = b1.decode("utf-8")
    assert SDK_SCHEMA_VERSION in text
    assert "x-generated-at" not in text
    assert "generatedAt" not in text
    # No wall-clock / ISO timestamp fingerprints
    assert "x-generated-at" not in text


def test_unverified_partial_absent_from_sdk():
    reg = reset_api_registry()
    reg.register(
        {
            "name": "Partial Only",
            "method": "POST",
            "path": "/v1/apps/probe-app/actions/partial",
            "source": "virtual_adapter",
            "application_id": "probe-app",
            "capability": "probe-app.partial",
            "status": "PARTIAL",
            "permissions": ["ui.read"],
            "description": "not verified",
        }
    )
    _register_verified(reg)
    from windows_os_api.api.rest.apis import _registry_openapi_document

    doc = _registry_openapi_document(visible_app_ids=None)
    source = generate_python_sdk(doc)
    assert "/v1/apps/probe-app/actions/search" in source
    assert "/v1/apps/probe-app/actions/partial" not in source
    assert "partial" not in source.lower() or "Partial Only" not in source


def test_no_secret_leakage_in_sdk_output(monkeypatch):
    monkeypatch.setenv("WINOS_AI_API_KEY", "super-secret-n025-planted-key")
    monkeypatch.setenv("WINOS_FAKE_TOKEN", "planted-bearer-token-n025")
    os.environ["WINOS_AI_API_KEY"] = "super-secret-n025-planted-key"

    reg = reset_api_registry()
    _register_verified(reg)
    from windows_os_api.api.rest.apis import _registry_openapi_document

    doc = _registry_openapi_document(visible_app_ids=None)
    source = generate_python_sdk(doc)
    assert "super-secret-n025-planted-key" not in source
    assert "planted-bearer-token-n025" not in source
    assert "dev-key-change-me" not in source
    assert "admin-key-change-me" not in source
    # Credential only via constructor param name, never a literal value
    assert "api_key: str | None = None" in source or "api_key=" in source


def test_generated_client_calls_real_route(client, auth_headers, tmp_sandbox):
    created = client.post(
        "/v1/apps/contoso-crm/adapter", headers=auth_headers, json={"hwnd": 1001}
    )
    assert created.status_code == 200, created.text
    adapter = get_adapter("contoso-crm")
    edit = next(a for a in adapter.actions if a.control_type == "Edit")
    verified = verify_and_record("contoso-crm", edit.name, times=2)
    assert verified["verification"]["state"] == "VERIFIED"

    r = client.get("/v1/apps/contoso-crm/sdk.py", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "text/x-python" in r.headers.get("content-type", "")
    source = r.text
    assert f"/v1/apps/contoso-crm/actions/{edit.name}" in source

    mod = load_generated_sdk(source)
    from windows_os_api.apps.schema.openapi_export import make_operation_id
    from windows_os_api.apps.schema.sdk_export import _safe_ident

    expected = _safe_ident(
        make_operation_id(app_id="contoso-crm", action_name=edit.name)
    )
    sdk = mod.CLIENT_CLASS(
        base_url="http://testserver",
        api_key="dev-key-change-me",
        timeout=10.0,
        client=_sdk_http_client(client),
    )
    assert hasattr(sdk, expected), f"missing {expected}; have {[n for n in dir(sdk) if n.startswith('app__')]}"
    result = getattr(sdk, expected)(params={"value": "n025-sdk-effect"})
    assert isinstance(result, dict)
    assert result.get("ok") is True


def test_generated_client_surfaces_403(client, auth_headers, viewer_headers, tmp_sandbox):
    created = client.post(
        "/v1/apps/contoso-crm/adapter", headers=auth_headers, json={"hwnd": 1001}
    )
    assert created.status_code == 200
    adapter = get_adapter("contoso-crm")
    edit = next(a for a in adapter.actions if a.control_type == "Edit")
    assert verify_and_record("contoso-crm", edit.name)["verification"]["state"] == "VERIFIED"

    source = client.get("/v1/apps/contoso-crm/sdk.py", headers=auth_headers).text
    mod = load_generated_sdk(source)
    from windows_os_api.apps.schema.openapi_export import make_operation_id
    from windows_os_api.apps.schema.sdk_export import _safe_ident

    expected = _safe_ident(make_operation_id(app_id="contoso-crm", action_name=edit.name))

    sdk = mod.CLIENT_CLASS(
        base_url="http://testserver",
        api_key="viewer-key-n011",  # SYSTEM_READ only — no ADAPTER_USE
        timeout=10.0,
        client=_sdk_http_client(client),
    )
    with pytest.raises(mod.SdkForbidden) as ei:
        getattr(sdk, expected)(params={"value": "denied"})
    assert ei.value.status_code == 403


def test_generated_client_timeout_path():
    doc = finalize_openapi_export(
        {
            "openapi": "3.0.3",
            "info": {"title": "Timeout Probe", "version": "1.0.0", "x-schema-version": "n019-1"},
            "paths": {
                "/v1/health": {
                    "get": {
                        "operationId": "platform__health_get_n025",
                        "responses": {"200": {"description": "ok"}},
                        "x-risk": "low",
                        "x-scopes": ["system.read"],
                        "x-auth": {"required": True, "schemes": ["ApiKeyAuth"]},
                        "x-version": "1.0.0",
                        "x-schema-version": "n019-1",
                        "x-crud": "read",
                    }
                }
            },
            "components": {
                "securitySchemes": {
                    "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
                }
            },
        }
    )
    source = generate_python_sdk(doc)
    mod = load_generated_sdk(source)

    def hang_transport(method, url, headers, data, timeout):
        raise TimeoutError("simulated hang")

    sdk = mod.CLIENT_CLASS(base_url="http://example.invalid", timeout=0.01, transport=hang_transport)
    with pytest.raises(mod.SdkTimeout):
        sdk.platform__health_get_n025()


def test_revoke_demote_regen_drops_op_and_live_fail_closed(client, auth_headers, tmp_sandbox):
    reg = reset_api_registry()
    rec = _register_verified(reg)

    # Also wire a real adapter so live invoke works before demote
    client.post("/v1/apps/probe-app/adapter", headers=auth_headers, json={"hwnd": 2002})
    adapter = get_adapter("probe-app")
    # Map registry path action "search" — may not exist on fake adapter; use
    # registry disable + SDK regen as primary revoke signal, and gateway 403
    # via DISABLED on a real contoso path for live fail-closed.

    from windows_os_api.api.rest.apis import _registry_openapi_document

    doc1 = _registry_openapi_document(visible_app_ids=None)
    src1 = generate_python_sdk(doc1)
    assert rec.path in src1

    reg.set_status(rec.id, ApiStatus.DISABLED)
    doc2 = _registry_openapi_document(visible_app_ids=None)
    src2 = generate_python_sdk(doc2)
    assert rec.path not in src2
    assert canonical_sdk_bytes(doc1) != canonical_sdk_bytes(doc2)

    # Live fail-closed: verified action then disable via registry path matching
    # a real invoke route.
    created = client.post(
        "/v1/apps/contoso-crm/adapter", headers=auth_headers, json={"hwnd": 1001}
    )
    assert created.status_code == 200
    adapter = get_adapter("contoso-crm")
    edit = next(a for a in adapter.actions if a.control_type == "Edit")
    assert verify_and_record("contoso-crm", edit.name)["verification"]["state"] == "VERIFIED"
    path = f"/v1/apps/contoso-crm/actions/{edit.name}"
    live_rec = reg.register(
        {
            "name": "Contoso Edit",
            "method": "POST",
            "path": path,
            "source": "virtual_adapter",
            "application_id": "contoso-crm",
            "capability": f"contoso-crm.{edit.name}",
            "status": "VERIFIED",
            "verification_id": "ver_n025_contoso_edit",
            "last_verified_at": time.time(),
            "permissions": ["ui.control"],
            "authentication_required": True,
        }
    )
    src_ok = client.get("/v1/apps/contoso-crm/sdk.py", headers=auth_headers).text
    assert path in src_ok
    mod = load_generated_sdk(src_ok)
    from windows_os_api.apps.schema.openapi_export import make_operation_id
    from windows_os_api.apps.schema.sdk_export import _safe_ident

    expected = _safe_ident(make_operation_id(app_id="contoso-crm", action_name=edit.name))

    sdk = mod.CLIENT_CLASS(
        base_url="http://testserver",
        api_key="dev-key-change-me",
        client=_sdk_http_client(client),
    )
    assert getattr(sdk, expected)(params={"value": "before-revoke"}).get("ok") is True

    reg.set_status(live_rec.id, ApiStatus.DISABLED)
    # Regen drops registry op; per-app SDK still has verified adapter action —
    # capability revoke for registry export:
    doc_after = _registry_openapi_document(visible_app_ids=None)
    assert path not in generate_python_sdk(doc_after)

    # Gateway fail-closed when registry record DISABLED for that path
    denied = client.post(path, headers=auth_headers, json={"params": {"value": "after"}})
    assert denied.status_code == 403, denied.text

    # Auth key revoke also blocks SDK calls
    get_auth_registry().revoke_key("dev-key-change-me")
    sdk2 = mod.CLIENT_CLASS(
        base_url="http://testserver",
        api_key="dev-key-change-me",
        client=_sdk_http_client(client),
    )
    with pytest.raises(Exception) as ei:
        getattr(sdk2, expected)(params={"value": "revoked-key"})
    status = getattr(ei.value, "status_code", None)
    assert status in (401, 403) or "revoked" in str(ei.value).lower() or "403" in str(ei.value)


def test_http_registry_sdk_and_missing_adapter(client, auth_headers, viewer_headers):
    reg = reset_api_registry()
    rec = _register_verified(reg)
    r = client.get("/v1/apis/sdk.py", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "text/x-python" in r.headers.get("content-type", "")
    assert rec.path in r.text
    # Viewer can read registry SDK (SYSTEM_READ)
    r_v = client.get("/v1/apis/sdk.py", headers=viewer_headers)
    assert r_v.status_code == 200
    assert r.text == r_v.text  # deterministic twin over HTTP

    missing = client.get("/v1/apps/no-such-app-n025/sdk.py", headers=auth_headers)
    assert missing.status_code == 404

    with pytest.raises(AdapterOpenAPINotFound):
        app_sdk("missing-app-n025-sdk")


def test_app_sdk_matches_openapi_operations(tmp_sandbox):
    adapter = create_adapter("contoso-crm", hwnd=1001)
    edit = next(a for a in adapter.actions if a.control_type == "Edit")
    assert verify_and_record("contoso-crm", edit.name)["verification"]["state"] == "VERIFIED"
    doc = app_openapi("contoso-crm")
    source = app_sdk("contoso-crm")
    assert canonical_sdk_bytes(doc) == source.encode("utf-8")
    for path in doc["paths"]:
        assert path in source
