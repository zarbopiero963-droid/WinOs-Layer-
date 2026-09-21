"""N019 — Deterministic OpenAPI + verified CRUD mapping (H63-N019)."""
from __future__ import annotations

import json
import time

import pytest

from windows_os_api.apps.adapters.engine import (
    create_adapter,
    generate_adapter_openapi,
    get_adapter,
    reset_adapters,
    verify_and_record,
)
from windows_os_api.apps.api_registry.model import (
    issue_verification_proof,
    ApiRegistry,
    ApiStatus,
    reset_api_registry,
)
from windows_os_api.apps.schema.generator import AdapterOpenAPINotFound, app_openapi
from windows_os_api.apps.schema.openapi_export import (
    OpenAPISchemaRejected,
    canonical_openapi_bytes,
    finalize_openapi_export,
    make_operation_id,
    validate_openapi_document,
    VOLATILE_OPENAPI_FIELDS,
)


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
        "verification_id": issue_verification_proof("ver_n019_probe_1"),
        "last_verified_at": now,
        "permissions": ["ui.read", "ui.control"],
        "authentication_required": True,
    }
    payload.update(overrides)
    return reg.register(payload)


def test_twin_exports_identical_bytes_except_documented_volatiles():
    reg = reset_api_registry()
    _register_verified(reg)
    from windows_os_api.api.rest.apis import _registry_openapi_document

    d1 = _registry_openapi_document(visible_app_ids=None)
    d2 = _registry_openapi_document(visible_app_ids=None)
    b1 = canonical_openapi_bytes(d1)
    b2 = canonical_openapi_bytes(d2)
    assert b1 == b2
    assert VOLATILE_OPENAPI_FIELDS  # documented set exists
    # No volatile fields present in export
    blob = json.loads(b1.decode())
    assert "x-generated-at" not in json.dumps(blob)


def test_unverified_capability_absent_from_registry_export():
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
    paths = doc["paths"]
    assert "/v1/apps/probe-app/actions/search" in paths
    assert "/v1/apps/probe-app/actions/partial" not in paths
    op = paths["/v1/apps/probe-app/actions/search"]["post"]
    assert op["x-verification-state"] == "VERIFIED"
    assert op["x-crud"] == "create"
    assert op["x-risk"]
    assert isinstance(op["x-scopes"], list)
    assert "required" in op["x-auth"]
    assert op["x-version"]
    assert "200" in op["responses"] and "content" in op["responses"]["200"]
    assert "403" in op["responses"]


def test_invalid_schema_rejected():
    bad = {"openapi": "3.0.3", "info": {"title": "x", "version": "1"}, "paths": {}}
    # empty paths is valid structurally — craft duplicate op ids
    bad["paths"] = {
        "/a": {
            "get": {
                "operationId": "same",
                "responses": {"200": {"description": "ok"}},
                "x-risk": "low",
                "x-scopes": [],
                "x-auth": {"required": True},
                "x-version": "1",
            }
        },
        "/b": {
            "get": {
                "operationId": "same",
                "responses": {"200": {"description": "ok"}},
                "x-risk": "low",
                "x-scopes": [],
                "x-auth": {"required": True},
                "x-version": "1",
            }
        },
    }
    with pytest.raises(OpenAPISchemaRejected):
        validate_openapi_document(bad)

    with pytest.raises(OpenAPISchemaRejected):
        finalize_openapi_export(
            {
                "openapi": "9.9.9",
                "info": {"title": "t", "version": "1"},
                "paths": {},
            }
        )


def test_app_openapi_does_not_implicitly_create_adapter():
    with pytest.raises(AdapterOpenAPINotFound):
        app_openapi("missing-app-n019")
    assert get_adapter("missing-app-n019") is None


def test_per_app_openapi_unique_operation_ids_and_crud_mapping(tmp_sandbox):
    adapter = create_adapter("contoso-crm", hwnd=1001)
    edit = next(a for a in adapter.actions if a.control_type == "Edit")
    result = verify_and_record("contoso-crm", edit.name)
    assert result["verification"]["state"] == "VERIFIED"
    doc = generate_adapter_openapi(adapter)
    path = f"/v1/apps/contoso-crm/actions/{edit.name}"
    assert path in doc["paths"]
    op = doc["paths"][path]["post"]
    assert op["operationId"] == make_operation_id(
        app_id="contoso-crm", action_name=edit.name
    )
    assert op["x-crud"] == "update"
    assert op["x-verification-id"]
    # twin export bytes
    assert canonical_openapi_bytes(doc) == canonical_openapi_bytes(
        generate_adapter_openapi(adapter)
    )


def test_http_registry_openapi_and_root_merge(client, auth_headers):
    reg = reset_api_registry()
    rec = _register_verified(reg)
    r = client.get("/v1/apis/openapi.json", headers=auth_headers)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert rec.path in doc["paths"]
    assert doc["paths"][rec.path]["post"]["x-api-id"] == rec.id

    # unverified absent
    reg.register(
        {
            "name": "No",
            "method": "DELETE",
            "path": "/v1/apps/probe-app/actions/nope",
            "source": "virtual_adapter",
            "application_id": "probe-app",
            "capability": "probe-app.nope",
            "status": "DISABLED",
            "permissions": [],
            "description": "disabled",
        }
    )
    r2 = client.get("/v1/apis/openapi.json", headers=auth_headers)
    assert "/v1/apps/probe-app/actions/nope" not in r2.json()["paths"]

    root = client.get("/openapi.json")
    assert root.status_code == 200
    root_doc = root.json()
    assert rec.path in root_doc["paths"], "registry VERIFIED path must merge into /openapi.json"
    assert "/v1/health" in root_doc["paths"]


def test_client_invokes_real_effect_from_published_path(client, auth_headers, tmp_sandbox):
    """Published OpenAPI path maps to real gateway invoke (CRUD mapping)."""
    created = client.post(
        "/v1/apps/contoso-crm/adapter", headers=auth_headers, json={"hwnd": 1001}
    )
    assert created.status_code == 200, created.text
    adapter = get_adapter("contoso-crm")
    edit = next(a for a in adapter.actions if a.control_type == "Edit")
    verified = client.post(
        f"/v1/apps/contoso-crm/actions/{edit.name}/verify",
        headers=auth_headers,
        json={"times": 2},
    )
    assert verified.status_code == 200, verified.text
    schema = client.get(
        f"/v1/apps/contoso-crm/openapi.json", headers=auth_headers
    ).json()
    path = f"/v1/apps/contoso-crm/actions/{edit.name}"
    assert path in schema["paths"]
    invoked = client.post(
        path,
        headers=auth_headers,
        json={"params": {"value": "n019-real-effect"}},
    )
    assert invoked.status_code == 200, invoked.text
    assert invoked.json().get("ok") is True


def test_http_openapi_missing_adapter_is_404(client, auth_headers):
    r = client.get("/v1/apps/no-such-app/openapi.json", headers=auth_headers)
    assert r.status_code == 404


def test_unbound_registry_path_omitted_from_export():
    """N019 audit: /audit-only must not export without an HTTP route binding."""
    from windows_os_api.api.rest.apis import _registry_openapi_document
    from windows_os_api.apps.schema.openapi_export import http_path_is_bound
    from windows_os_api.apps.schema.sdk_export import generate_python_sdk

    assert http_path_is_bound("/audit-only", "POST") is False
    assert http_path_is_bound(
        "/v1/apps/probe-app/actions/search", "POST"
    ) is True

    reg = reset_api_registry()
    _register_verified(
        reg,
        path="/audit-only",
        capability="private-app.edit",
        application_id="private-app",
        name="Audit fixture",
    )
    _register_verified(reg)  # bound /v1/apps/probe-app/actions/search
    doc = _registry_openapi_document(visible_app_ids=None)
    assert "/audit-only" not in doc["paths"]
    assert "/v1/apps/probe-app/actions/search" in doc["paths"]
    sdk = generate_python_sdk(doc)
    assert "/audit-only" not in sdk
    assert "probe-app" in sdk or "search" in sdk


def test_stale_verification_omitted_from_export():
    """N019: export re-evaluates verification age — stale VERIFIED is omitted."""
    from dataclasses import replace

    from windows_os_api.api.rest.apis import _registry_openapi_document
    from windows_os_api.apps.api_registry import model as model_mod
    from windows_os_api.apps.api_registry.model import ApiRegistry

    # Short max-age so export freshness fails without waiting 7d.
    reg = ApiRegistry(verification_max_age_sec=60)
    model_mod._REGISTRY = reg
    try:
        now = time.time()
        stale = _register_verified(
            reg,
            path="/v1/apps/probe-app/actions/stale",
            capability="probe-app.stale",
            name="Stale Search",
            last_verified_at=now,
            verification_id=issue_verification_proof("ver_n019_stale"),
        )
        fresh = _register_verified(
            reg,
            path="/v1/apps/probe-app/actions/fresh",
            capability="probe-app.fresh",
            name="Fresh Search",
            verification_id=issue_verification_proof("ver_n019_fresh"),
            last_verified_at=now,
        )
        # register() demotes stale timestamps; mutate after insert to simulate
        # a previously-VERIFIED record whose evidence aged out before export.
        forced = replace(stale, status=ApiStatus.VERIFIED, last_verified_at=now - 120)
        with reg._lock:
            reg._by_id[forced.id] = forced
        doc = _registry_openapi_document(visible_app_ids=None)
        assert "/v1/apps/probe-app/actions/stale" not in doc["paths"]
        assert fresh.path in doc["paths"]
    finally:
        reset_api_registry()
