"""N014 / H63-N014 — API Registry model and authoritative #61 states.

Unit matrix only (Q08). Full installed W/L H63-N014 is MANUAL (#21).
Coverage refs: R31 R33 W061 W070 L061 L070 S61-01–S61-03 G28 G29.
"""
from __future__ import annotations

import time

import pytest

from windows_os_api.apps.api_registry import (
    API_REGISTRY_SCHEMA_VERSION,
    ApiRegistry,
    ApiStatus,
    RegistrationRejected,
    compute_api_id,
    get_api_registry,
    map_d3_envelope_to_status,
    map_lifecycle_to_status,
    reset_api_registry,
)
from windows_os_api.apps.api_registry.model import (
    DEFAULT_VERIFICATION_MAX_AGE_SEC,
    authorize_verified_status,
    issue_verification_proof,
    clear_verification_proofs,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_api_registry()
    yield
    reset_api_registry()


def _base(**overrides):
    payload = {
        "name": "Customer Search",
        "method": "POST",
        "path": "/v1/apps/example/customer/search",
        "description": "Search customer in desktop application",
        "source": "virtual_adapter",
        "application_id": "example-app",
        "adapter_id": "adapter_demo",
        "capability": "customer.search",
        "permissions": ["ui.read", "ui.control"],
        "authentication_required": True,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Schema + six states
# ---------------------------------------------------------------------------


def test_six_authoritative_states_exist():
    assert {s.value for s in ApiStatus} == {
        "VERIFIED",
        "PARTIAL",
        "RESTRICTED",
        "UNSUPPORTED",
        "ERROR",
        "DISABLED",
    }


def test_schema_version_present_on_record():
    reg = get_api_registry()
    rec = reg.register(_base(status="PARTIAL"))
    assert rec.schema_version == API_REGISTRY_SCHEMA_VERSION == "1"
    assert rec.to_dict()["schema_version"] == "1"
    assert set(rec.to_dict()) >= {
        "id",
        "name",
        "method",
        "path",
        "description",
        "source",
        "application_id",
        "adapter_id",
        "capability",
        "status",
        "permissions",
        "authentication_required",
        "created_at",
        "updated_at",
        "last_verified_at",
        "verification_id",
        "schema_version",
    }


# ---------------------------------------------------------------------------
# GENERATED != VERIFIED / missing / false / stale metadata
# ---------------------------------------------------------------------------


def test_generated_lifecycle_never_verified():
    reg = get_api_registry()
    rec = reg.register(_base(lifecycle="GENERATED"))
    assert rec.status is ApiStatus.PARTIAL
    assert rec.status is not ApiStatus.VERIFIED


def test_discovered_and_unknown_never_verified():
    reg = get_api_registry()
    assert reg.register(_base(lifecycle="DISCOVERED")).status is ApiStatus.PARTIAL
    assert reg.register(
        _base(path="/v1/a", capability="a", lifecycle="UNKNOWN")
    ).status is ApiStatus.PARTIAL
    assert reg.register(
        _base(path="/v1/b", capability="b", status="GENERATED")
    ).status is ApiStatus.PARTIAL


def test_missing_status_defaults_to_partial_not_verified():
    reg = get_api_registry()
    rec = reg.register(_base())  # no status
    assert rec.status is ApiStatus.PARTIAL


def test_verified_without_verification_id_demoted():
    reg = get_api_registry()
    now = time.time()
    rec = reg.register(
        _base(status="VERIFIED", last_verified_at=now, verification_id=None)
    )
    assert rec.status is ApiStatus.PARTIAL


def test_verified_without_last_verified_at_demoted():
    reg = get_api_registry()
    rec = reg.register(
        _base(status="VERIFIED", verification_id="verification_abc", last_verified_at=None)
    )
    assert rec.status is ApiStatus.PARTIAL


def test_verified_with_false_metadata_demoted():
    reg = get_api_registry()
    rec = reg.register(
        _base(
            status="VERIFIED",
            verification_id=False,
            last_verified_at=False,
        )
    )
    assert rec.status is ApiStatus.PARTIAL


def test_verified_with_empty_verification_id_demoted():
    reg = get_api_registry()
    rec = reg.register(
        _base(
            status="VERIFIED",
            verification_id="   ",
            last_verified_at=time.time(),
        )
    )
    assert rec.status is ApiStatus.PARTIAL


def test_stale_verification_never_verified():
    reg = ApiRegistry(verification_max_age_sec=3600)
    now = time.time()
    stale = now - 7200  # 2h > 1h max age
    rec = reg.register(
        _base(
            status="VERIFIED",
            verification_id="verification_stale",
            last_verified_at=stale,
        ),
        now=now,
    )
    assert rec.status is ApiStatus.PARTIAL


def test_future_timestamp_treated_as_false_metadata():
    reg = get_api_registry()
    now = time.time()
    rec = reg.register(
        _base(
            status="VERIFIED",
            verification_id="verification_future",
            last_verified_at=now + 86_400,
        ),
        now=now,
    )
    assert rec.status is ApiStatus.PARTIAL


def test_fresh_verified_evidence_accepted():
    reg = get_api_registry()
    now = time.time()
    vid = issue_verification_proof("verification_ok_001")
    rec = reg.register(
        _base(
            status="VERIFIED",
            verification_id=vid,
            last_verified_at=now - 10,
        ),
        now=now,
    )
    assert rec.status is ApiStatus.VERIFIED
    assert rec.verification_id == "verification_ok_001"


def test_invented_verification_id_and_timestamp_not_verified():
    """Audit H63-N014: invented id+timestamp alone must not mint VERIFIED."""
    reg = get_api_registry()
    now = time.time()
    rec = reg.register(
        _base(
            status="VERIFIED",
            verification_id="invented-not-from-engine",
            last_verified_at=now,
        ),
        now=now,
    )
    assert rec.status is ApiStatus.PARTIAL
    assert rec.last_verified_at == pytest.approx(now - 10)


def test_authorize_verified_helper_direct():
    now = time.time()
    assert (
        authorize_verified_status(
            status=ApiStatus.VERIFIED,
            verification_id=None,
            last_verified_at=now,
            now=now,
        )
        is ApiStatus.PARTIAL
    )
    assert (
        authorize_verified_status(
            status=ApiStatus.VERIFIED,
            verification_id="v1",
            last_verified_at=now - DEFAULT_VERIFICATION_MAX_AGE_SEC - 1,
            now=now,
        )
        is ApiStatus.PARTIAL
    )
    assert (
        authorize_verified_status(
            status=ApiStatus.PARTIAL,
            verification_id=None,
            last_verified_at=None,
            now=now,
        )
        is ApiStatus.PARTIAL
    )


# ---------------------------------------------------------------------------
# Deterministic duplicate registration
# ---------------------------------------------------------------------------


def test_duplicate_registration_deterministic_same_id():
    reg = get_api_registry()
    a = reg.register(_base(status="PARTIAL"))
    b = reg.register(_base(status="PARTIAL", description="updated desc"))
    assert a.id == b.id
    assert a.id.startswith("api_")
    assert len(reg.list()) == 1
    assert reg.get(a.id).description == "updated desc"


def test_compute_api_id_stable_across_calls():
    kwargs = dict(
        method="post",
        path="/v1/apps/example/customer/search",
        application_id="example-app",
        capability="customer.search",
        source="Virtual_Adapter",
    )
    assert compute_api_id(**kwargs) == compute_api_id(**kwargs)
    # method case-normalized
    assert compute_api_id(**kwargs) == compute_api_id(
        method="POST",
        path="/v1/apps/example/customer/search",
        application_id="example-app",
        capability="customer.search",
        source="virtual_adapter",
    )


def test_different_keys_different_ids():
    reg = get_api_registry()
    a = reg.register(_base(capability="customer.search"))
    b = reg.register(_base(capability="customer.create", path="/v1/apps/example/customer/create"))
    assert a.id != b.id
    assert len(reg.list()) == 2


def test_payload_id_does_not_override_deterministic_key():
    """Caller-supplied id must not create a second row for the same key."""
    reg = get_api_registry()
    first = reg.register(_base(status="PARTIAL"))
    second = reg.register(_base(status="PARTIAL", id="api_attacker_chosen"))
    assert second.id == first.id
    assert "api_attacker_chosen" not in {r.id for r in reg.list()}


# ---------------------------------------------------------------------------
# Lifecycle + D3 envelope mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lifecycle,expected",
    [
        ("VERIFIED", ApiStatus.VERIFIED),
        ("PARTIAL", ApiStatus.PARTIAL),
        ("RESTRICTED", ApiStatus.RESTRICTED),
        ("UNSUPPORTED", ApiStatus.UNSUPPORTED),
        ("ERROR", ApiStatus.ERROR),
        ("DISABLED", ApiStatus.DISABLED),
        ("GENERATED", ApiStatus.PARTIAL),
        ("DISCOVERED", ApiStatus.PARTIAL),
        ("FAILED", ApiStatus.ERROR),
        ("BLOCKED", ApiStatus.RESTRICTED),
        ("UNSTABLE", ApiStatus.PARTIAL),
        ("REVOKED", ApiStatus.DISABLED),
        (None, ApiStatus.PARTIAL),
        ("", ApiStatus.PARTIAL),
        ("weird", ApiStatus.PARTIAL),
    ],
)
def test_map_lifecycle_to_status(lifecycle, expected):
    assert map_lifecycle_to_status(lifecycle) is expected


def test_lifecycle_pass_still_gated_on_register():
    """map may say VERIFIED for PASS, but register demotes without evidence."""
    assert map_lifecycle_to_status("PASS") is ApiStatus.VERIFIED
    reg = get_api_registry()
    rec = reg.register(_base(lifecycle="PASS"))
    assert rec.status is ApiStatus.PARTIAL


def test_d3_envelope_mapping():
    assert (
        map_d3_envelope_to_status(
            {"supported": False, "error_code": "CAPABILITY_NOT_SUPPORTED", "printers": []}
        )
        is ApiStatus.UNSUPPORTED
    )
    assert (
        map_d3_envelope_to_status(
            {"supported": False, "error_code": "CAPABILITY_UNAVAILABLE", "items": []}
        )
        is ApiStatus.ERROR
    )
    assert (
        map_d3_envelope_to_status(
            {"supported": True, "error_code": "DISCOVERY_FAILED", "users": []}
        )
        is ApiStatus.ERROR
    )
    # Successful D3 discovery ≠ VERIFIED
    assert (
        map_d3_envelope_to_status({"supported": True, "users": [{"name": "a"}]})
        is ApiStatus.PARTIAL
    )
    assert map_d3_envelope_to_status(None) is ApiStatus.ERROR
    assert map_d3_envelope_to_status({}) is ApiStatus.ERROR


def test_register_via_d3_envelope_never_verified():
    reg = get_api_registry()
    rec = reg.register(
        {
            **_base(path="/v1/apps/example/other", capability="other.cap"),
            "d3": {"supported": True, "items": []},
        }
    )
    assert rec.status is ApiStatus.PARTIAL
    rec_u = reg.register(
        {
            **_base(path="/v1/apps/example/unsup", capability="other.unsup"),
            "d3": {
                "supported": False,
                "error_code": "CAPABILITY_NOT_SUPPORTED",
                "items": [],
            },
        }
    )
    assert rec_u.status is ApiStatus.UNSUPPORTED


def test_register_rejects_incomplete_required_fields():
    reg = get_api_registry()
    with pytest.raises(RegistrationRejected):
        reg.register({"name": "x"})  # missing method/path/…


def test_set_status_cannot_promote_to_verified_without_evidence():
    reg = get_api_registry()
    rec = reg.register(_base(status="PARTIAL"))
    updated = reg.set_status(rec.id, "VERIFIED")
    assert updated.status is ApiStatus.PARTIAL


def test_disabled_and_restricted_roundtrip():
    reg = get_api_registry()
    d = reg.register(_base(status="DISABLED", path="/v1/disabled", capability="x.disabled"))
    r = reg.register(_base(status="RESTRICTED", path="/v1/restricted", capability="x.restricted"))
    assert d.status is ApiStatus.DISABLED
    assert r.status is ApiStatus.RESTRICTED
