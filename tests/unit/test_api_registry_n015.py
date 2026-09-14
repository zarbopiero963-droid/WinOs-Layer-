"""N015 / H63-N015 — API Registry persistence and projections.

Unit matrix only (Q08/Q15). Full installed W/L H63-N015 is MANUAL (#21).
Coverage refs: R33 W070 L070 S61-13 S61-22 G29.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from windows_os_api.apps.api_registry import (
    API_REGISTRY_SCHEMA_VERSION,
    ApiRegistry,
    ApiStatus,
    PersistentApiRegistry,
    apply_projections,
    clear_registry_store,
    compute_api_id,
    load_registry,
    project_adapter_apis,
    project_native_apis,
    project_workflow_apis,
    save_registry,
    store_path,
)
from windows_os_api.apps.api_registry.store import (
    RECORD_ID_MISMATCH,
    STORE_RECOVERED_FROM_BAK,
    STORE_UNREADABLE,
    STORE_VERSION_UNKNOWN,
    bak_path,
)


@pytest.fixture
def store_tmpdir(tmp_path, monkeypatch):
    monkeypatch.setenv("WINOS_API_REGISTRY_STORE", str(tmp_path))
    clear_registry_store()
    yield tmp_path
    clear_registry_store()


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


# ---------------------------------------------------------------------------
# Round-trip / process restart
# ---------------------------------------------------------------------------


def test_round_trip_new_registry_keeps_same_id(store_tmpdir):
    reg = ApiRegistry()
    now = time.time()
    rec = reg.register(
        _base(
            status="VERIFIED",
            verification_id="verification_n015",
            last_verified_at=now,
        )
    )
    api_id = rec.id
    assert rec.status is ApiStatus.VERIFIED
    save_registry(reg)

    report = load_registry()
    assert report.source_path is not None
    reloaded = report.registry
    assert reloaded is not reg
    got = reloaded.get(api_id)
    assert got is not None
    assert got.id == api_id
    assert got.id == compute_api_id(
        method="POST",
        path="/v1/apps/example/customer/search",
        application_id="example-app",
        capability="customer.search",
        source="virtual_adapter",
    )
    assert got.status is ApiStatus.VERIFIED
    assert got.verification_id == "verification_n015"
    assert got.schema_version == API_REGISTRY_SCHEMA_VERSION


def test_persistent_registry_saves_on_register(store_tmpdir):
    preg = PersistentApiRegistry()
    rec = preg.register(_base(path="/v1/a", capability="a"))
    assert store_path().exists()
    report = load_registry()
    assert report.registry.get(rec.id) is not None


# ---------------------------------------------------------------------------
# Corrupt / bak recovery / both bad
# ---------------------------------------------------------------------------


def test_truncated_primary_recovers_from_bak(store_tmpdir):
    reg = ApiRegistry()
    rec = reg.register(_base())
    save_registry(reg)
    # Second save moves good → bak
    reg.register(_base(path="/v1/other", capability="other", name="Other"))
    save_registry(reg)

    # Corrupt primary; bak should still have at least the earlier state
    store_path().write_text("{truncated", encoding="utf-8")
    # Ensure bak exists and is valid JSON with records
    assert bak_path().exists()

    report = load_registry()
    assert report.recovered_from_bak is True
    assert any(s.code == STORE_RECOVERED_FROM_BAK for s in report.skipped)
    assert any(s.code == STORE_UNREADABLE for s in report.skipped)
    # Executable registry must not be empty if bak was good
    assert len(report.registry.list()) >= 1
    # No silent VERIFIED from garbage
    assert all(r.status is not ApiStatus.VERIFIED or r.verification_id for r in report.registry.list())


def test_both_corrupt_yields_empty_and_reported_errors(store_tmpdir):
    store_path().parent.mkdir(parents=True, exist_ok=True)
    store_path().write_text("{not-json", encoding="utf-8")
    bak_path().write_text("[[[", encoding="utf-8")

    report = load_registry()
    assert report.registry.list() == []
    assert report.recovered_from_bak is False
    codes = {s.code for s in report.skipped}
    assert STORE_UNREADABLE in codes
    # Must not publish fake VERIFIED
    assert not any(r.status is ApiStatus.VERIFIED for r in report.registry.list())


def test_wrong_store_format_version_fail_closed(store_tmpdir):
    store_path().parent.mkdir(parents=True, exist_ok=True)
    store_path().write_text(
        json.dumps(
            {
                "store_format_version": 99,
                "schema_version": "1",
                "records": [_base(status="VERIFIED", verification_id="x", last_verified_at=time.time())],
            }
        ),
        encoding="utf-8",
    )
    report = load_registry()
    assert report.registry.list() == []
    assert any(s.code == STORE_VERSION_UNKNOWN for s in report.skipped)


def test_persisted_verified_without_evidence_demoted_on_reload(store_tmpdir):
    """Never trust persisted status alone — re-run authorize gate."""
    reg = ApiRegistry()
    # Manually craft a store file claiming VERIFIED without evidence
    store_path().parent.mkdir(parents=True, exist_ok=True)
    forged = _base(status="VERIFIED", verification_id=None, last_verified_at=None)
    forged["id"] = compute_api_id(
        method=forged["method"],
        path=forged["path"],
        application_id=forged["application_id"],
        capability=forged["capability"],
        source=forged["source"],
    )
    store_path().write_text(
        json.dumps(
            {
                "store_format_version": 1,
                "schema_version": "1",
                "records": [forged],
            }
        ),
        encoding="utf-8",
    )
    report = load_registry()
    assert len(report.registry.list()) == 1
    assert report.registry.list()[0].status is ApiStatus.PARTIAL


# ---------------------------------------------------------------------------
# Collision / forged id
# ---------------------------------------------------------------------------


def test_forged_id_disagreeing_with_natural_key_rejected(store_tmpdir):
    store_path().parent.mkdir(parents=True, exist_ok=True)
    payload = _base()
    payload["id"] = "api_forged_not_matching_key_xx"
    store_path().write_text(
        json.dumps(
            {
                "store_format_version": 1,
                "schema_version": "1",
                "records": [payload],
            }
        ),
        encoding="utf-8",
    )
    report = load_registry()
    assert report.registry.list() == []
    assert any(s.code == RECORD_ID_MISMATCH for s in report.skipped)


# ---------------------------------------------------------------------------
# Projections — never trust manifest for VERIFIED
# ---------------------------------------------------------------------------


@dataclass
class _FakeAction:
    name: str
    description: str = ""
    params: list[str] = field(default_factory=list)
    risk: str = "low"
    verification: Any = None
    automation_id: str = ""


@dataclass
class _FakeAdapter:
    app_id: str
    actions: list[_FakeAction]
    adapter_id: str = ""


def test_adapter_projection_manifest_verified_stays_partial():
    adapter = _FakeAdapter(
        app_id="demo",
        actions=[
            _FakeAction(
                name="save",
                description="Save",
                verification={"state": "VERIFIED", "verified_at": time.time()},
            )
        ],
    )
    payloads = project_adapter_apis(adapter)
    assert len(payloads) == 1
    assert payloads[0]["status"] == ApiStatus.PARTIAL.value
    assert "verification_id" not in payloads[0]

    reg = ApiRegistry()
    rec = apply_projections(reg, payloads)[0]
    assert rec.status is ApiStatus.PARTIAL
    assert rec.status is not ApiStatus.VERIFIED


def test_workflow_projection_manifest_verified_stays_partial():
    wf = {
        "id": "wf-123",
        "name": "auto:save",
        "app_id": "demo",
        "risk": "medium",
        "verification": {"state": "VERIFIED"},
    }
    payloads = project_workflow_apis(wf)
    assert payloads[0]["status"] == ApiStatus.PARTIAL.value
    reg = ApiRegistry()
    rec = reg.register(payloads[0])
    assert rec.status is ApiStatus.PARTIAL


def test_native_projection_never_verified():
    payloads = project_native_apis()
    assert len(payloads) >= 3
    assert all(p["source"] == "native" for p in payloads)
    assert all(p["status"] == ApiStatus.PARTIAL.value for p in payloads)
    reg = ApiRegistry()
    for p in payloads:
        assert reg.register(p).status is ApiStatus.PARTIAL


def test_verified_only_with_registry_evidence_after_reload(store_tmpdir):
    adapter = _FakeAdapter(
        app_id="demo",
        actions=[_FakeAction(name="click_ok", verification={"state": "VERIFIED"})],
    )
    now = time.time()
    payloads = project_adapter_apis(
        adapter,
        registry_evidence={
            "demo.click_ok": {
                "verification_id": "ver_independent_001",
                "last_verified_at": now,
            }
        },
    )
    assert payloads[0]["status"] == ApiStatus.VERIFIED.value

    preg = PersistentApiRegistry()
    rec = preg.register(payloads[0])
    assert rec.status is ApiStatus.VERIFIED
    api_id = rec.id

    report = load_registry()
    got = report.registry.get(api_id)
    assert got is not None
    assert got.status is ApiStatus.VERIFIED
    assert got.verification_id == "ver_independent_001"


def test_missing_store_loads_empty(store_tmpdir):
    report = load_registry()
    assert report.registry.list() == []
    assert report.skipped == []
