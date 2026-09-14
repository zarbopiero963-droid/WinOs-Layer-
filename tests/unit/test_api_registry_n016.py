"""N016 / H63-N016 — API catalog list, detail, search, filters.

Unit/API matrix only (Q08). Full installed W/L H63-N016 is MANUAL (#21).
Coverage refs: R31 R32 W062 W070 L062 L070 S61-04–S61-05 G28 G29.
"""
from __future__ import annotations

import time

import pytest

from windows_os_api.apps.api_registry import (
    ApiRegistry,
    CatalogScopeDenied,
    RegistryUnavailable,
    get_catalog_record,
    list_catalog,
    reset_api_registry,
)
from windows_os_api.apps.api_registry.catalog import resolve_registry
from windows_os_api.core.security.auth import get_auth_registry, reset_auth_registry


def _base(**overrides):
    payload = {
        "name": "Customer Search",
        "method": "POST",
        "path": "/v1/apps/example/customer/search",
        "description": "Search customer",
        "source": "virtual_adapter",
        "application_id": "example-app",
        "adapter_id": "adapter_demo",
        "capability": "customer.search",
        "permissions": ["ui.read"],
        "authentication_required": True,
        "status": "PARTIAL",
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def registry():
    reg = reset_api_registry()
    yield reg
    reset_api_registry()


# ---------------------------------------------------------------------------
# Catalog helper: compare with registry / filters / pagination
# ---------------------------------------------------------------------------


def test_list_matches_registry_contents(registry):
    a = registry.register(_base(path="/v1/a", capability="a.cap", name="Alpha"))
    b = registry.register(
        _base(
            path="/v1/b",
            capability="b.cap",
            name="Beta",
            application_id="other-app",
            source="native",
            status="DISABLED",
            permissions=["system.read"],
        )
    )
    page = list_catalog(registry=registry, limit=100, offset=0)
    ids = {r.id for r in page.items}
    assert ids == {a.id, b.id}
    assert page.total == 2
    assert {r.id for r in registry.list()} == ids


def test_pagination_no_duplicate_ids_across_pages(registry):
    created = []
    for i in range(7):
        created.append(
            registry.register(
                _base(
                    path=f"/v1/apps/example/item/{i}",
                    capability=f"item.{i}",
                    name=f"Item {i}",
                )
            )
        )
    seen: set[str] = set()
    offset = 0
    limit = 3
    pages = 0
    while offset < 7:
        page = list_catalog(registry=registry, limit=limit, offset=offset)
        page_ids = [r.id for r in page.items]
        assert len(page_ids) == len(set(page_ids))
        assert not (seen & set(page_ids))
        seen.update(page_ids)
        offset += limit
        pages += 1
    assert pages == 3
    assert seen == {r.id for r in created}
    assert len(seen) == 7


def test_search_and_filters(registry):
    registry.register(
        _base(
            name="Find Me",
            path="/v1/apps/alpha/find",
            capability="alpha.find",
            application_id="alpha",
            source="workflow",
            status="VERIFIED",
            verification_id="v1",
            last_verified_at=time.time(),
            permissions=["ui.read", "adapter.use"],
        )
    )
    registry.register(
        _base(
            name="Other",
            path="/v1/apps/beta/other",
            capability="beta.other",
            application_id="beta",
            source="native",
            status="ERROR",
            permissions=["system.read"],
        )
    )
    by_q = list_catalog(registry=registry, q="FIND")
    assert by_q.total == 1
    assert by_q.items[0].name == "Find Me"

    by_src = list_catalog(registry=registry, source="native")
    assert by_src.total == 1
    assert by_src.items[0].application_id == "beta"

    by_app = list_catalog(registry=registry, application_id="alpha")
    assert by_app.total == 1

    by_status = list_catalog(registry=registry, status="ERROR")
    assert by_status.total == 1

    by_perm = list_catalog(registry=registry, permission="adapter.use")
    assert by_perm.total == 1

    by_perms = list_catalog(registry=registry, permissions=["system.read", "nope"])
    assert by_perms.total == 1


def test_cross_app_filter_denied_when_scoped(registry):
    registry.register(_base(application_id="allowed-app", path="/v1/x", capability="x"))
    with pytest.raises(CatalogScopeDenied):
        list_catalog(
            registry=registry,
            application_id="other-app",
            visible_app_ids=frozenset({"allowed-app"}),
        )


def test_visibility_hides_out_of_scope_records(registry):
    allowed = registry.register(
        _base(application_id="allowed-app", path="/v1/a", capability="a")
    )
    registry.register(_base(application_id="secret-app", path="/v1/b", capability="b"))
    page = list_catalog(
        registry=registry,
        visible_app_ids=frozenset({"allowed-app"}),
    )
    assert page.total == 1
    assert page.items[0].id == allowed.id


def test_missing_record_vs_registry_unavailable(registry):
    assert get_catalog_record("api_does_not_exist", registry=registry) is None

    def _boom():
        raise RuntimeError("disk gone")

    import windows_os_api.apps.api_registry.catalog as catalog_mod

    original = catalog_mod.get_api_registry
    catalog_mod.get_api_registry = lambda: (_ for _ in ()).throw(RuntimeError("disk gone"))  # type: ignore
    try:
        with pytest.raises(RegistryUnavailable):
            resolve_registry()
        with pytest.raises(RegistryUnavailable):
            get_catalog_record("anything")
    finally:
        catalog_mod.get_api_registry = original


# ---------------------------------------------------------------------------
# REST surface via TestClient
# ---------------------------------------------------------------------------


def test_rest_list_and_detail_match_registry(client, auth_headers, registry):
    rec = registry.register(
        _base(
            name="REST Visible",
            path="/v1/apps/example/rest",
            capability="rest.cap",
        )
    )
    r = client.get("/v1/apis", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "apis" in body and "total" in body and "limit" in body and "offset" in body
    ids = {a["id"] for a in body["apis"]}
    assert rec.id in ids
    assert body["total"] >= 1

    d = client.get(f"/v1/apis/{rec.id}", headers=auth_headers)
    assert d.status_code == 200
    assert d.json()["id"] == rec.id
    assert d.json()["name"] == "REST Visible"
    assert d.json()["schema_version"] == "1"


def test_rest_pagination_no_duplicates(client, auth_headers, registry):
    for i in range(5):
        registry.register(
            _base(
                path=f"/v1/apps/example/page/{i}",
                capability=f"page.{i}",
                name=f"Page {i}",
            )
        )
    seen: set[str] = set()
    for offset in (0, 2, 4):
        r = client.get(
            "/v1/apis",
            headers=auth_headers,
            params={"limit": 2, "offset": offset},
        )
        assert r.status_code == 200
        ids = [a["id"] for a in r.json()["apis"]]
        assert len(ids) == len(set(ids))
        assert not (seen & set(ids))
        seen.update(ids)


def test_rest_filters_search(client, auth_headers, registry):
    registry.register(
        _base(
            name="Needle Cap",
            path="/v1/apps/z/needle",
            capability="needle.cap",
            application_id="z-app",
            source="plugin",
            status="RESTRICTED",
            permissions=["ui.control"],
        )
    )
    registry.register(
        _base(
            name="Decoy",
            path="/v1/apps/y/decoy",
            capability="decoy.cap",
            application_id="y-app",
            source="native",
            status="PARTIAL",
        )
    )
    r = client.get("/v1/apis", headers=auth_headers, params={"q": "needle"})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["apis"][0]["capability"] == "needle.cap"

    r = client.get("/v1/apis", headers=auth_headers, params={"source": "plugin"})
    assert r.json()["total"] == 1

    r = client.get("/v1/apis", headers=auth_headers, params={"application_id": "z-app"})
    assert r.json()["total"] == 1

    r = client.get("/v1/apis", headers=auth_headers, params={"app": "z-app"})
    assert r.json()["total"] == 1

    r = client.get("/v1/apis", headers=auth_headers, params={"status": "RESTRICTED"})
    assert r.json()["total"] == 1

    r = client.get("/v1/apis", headers=auth_headers, params={"permission": "ui.control"})
    assert r.json()["total"] == 1


def test_rest_cross_user_app_filter_denied(client, auth_headers, registry):
    reset_auth_registry()
    get_auth_registry().set_app_scopes("dev-key-change-me", ["allowed-only"])
    registry.register(
        _base(application_id="allowed-only", path="/v1/ok", capability="ok")
    )
    registry.register(
        _base(application_id="other-user-app", path="/v1/no", capability="no")
    )
    try:
        r = client.get(
            "/v1/apis",
            headers=auth_headers,
            params={"application_id": "other-user-app"},
        )
        assert r.status_code == 403
        assert "scope" in r.json()["detail"].lower() or "denied" in r.json()["detail"].lower()

        # List without filter only shows allowed app
        r = client.get("/v1/apis", headers=auth_headers)
        assert r.status_code == 200
        apps = {a["application_id"] for a in r.json()["apis"]}
        assert "other-user-app" not in apps
        assert apps <= {"allowed-only"}
    finally:
        reset_auth_registry()


def test_rest_missing_404_vs_unavailable_503(client, auth_headers, registry, monkeypatch):
    r = client.get("/v1/apis/api_definitely_missing_n016", headers=auth_headers)
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()

    import windows_os_api.apps.api_registry.catalog as catalog_mod

    def _raise():
        raise RuntimeError("registry offline")

    monkeypatch.setattr(catalog_mod, "get_api_registry", _raise)
    r = client.get("/v1/apis/api_definitely_missing_n016", headers=auth_headers)
    assert r.status_code == 503
    assert "unavailable" in r.json()["detail"].lower()

    r = client.get("/v1/apis", headers=auth_headers)
    assert r.status_code == 503


def test_admin_sees_all_despite_scopes(client, admin_headers, registry):
    reset_auth_registry()
    # Even if somehow scopes were set, ADMIN bypasses
    get_auth_registry().set_app_scopes("admin-key-change-me", ["tiny"])
    registry.register(_base(application_id="tiny", path="/v1/t", capability="t"))
    registry.register(_base(application_id="wide", path="/v1/w", capability="w"))
    try:
        r = client.get("/v1/apis", headers=admin_headers)
        assert r.status_code == 200
        apps = {a["application_id"] for a in r.json()["apis"]}
        assert "tiny" in apps and "wide" in apps
    finally:
        reset_auth_registry()
