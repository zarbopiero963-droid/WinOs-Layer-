"""H63-N030 — Enforcement trust su load ed esecuzione (R22 R33 R48 W032 W097…)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from windows_os_api.apps.adapters.engine import (
    Adapter,
    AdapterAction,
    _adapters,
    invoke_action,
    load_persisted_adapters,
)
from windows_os_api.apps.adapters.store import MANIFEST_VERSION
from windows_os_api.apps.trust.keystore import (
    TrustKeystore,
    generate_ed25519_keypair,
    reset_keystore_for_tests,
)
from windows_os_api.apps.trust.signing import (
    resolve_trust_level,
    sign_adapter_manifest_ed25519,
)


@pytest.fixture
def trust_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ks = TrustKeystore(path=tmp_path / "keystore.json")
    reset_keystore_for_tests(ks)
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(tmp_path / "adapters"))
    (tmp_path / "adapters").mkdir()
    _adapters.clear()
    yield ks, tmp_path
    _adapters.clear()
    reset_keystore_for_tests(TrustKeystore(path=tmp_path / "empty.json"))


def _key(ks: TrustKeystore):
    priv, pub = generate_ed25519_keypair()
    ks.add_public_key(key_id="k1", publisher="Contoso", public_key_raw=pub, persist=True)
    return priv


def _signed_manifest(priv, *, automation_id: str = "btnOk"):
    body = {
        "manifest_version": MANIFEST_VERSION,
        "app_id": "crm",
        "app_name": "crm",
        "publisher": "Contoso",
        "actions": [
            {
                "name": "click_ok",
                "description": "ok",
                "automation_id": automation_id,
                "control_type": "Button",
                "params": [],
                "risk": "low",
            }
        ],
    }
    sig = sign_adapter_manifest_ed25519(body, key_id="k1", private_key=priv)
    body["signature"] = sig
    body["trust_level"] = "publisher"
    body["actions"][0]["verification"] = {
        "state": "VERIFIED",
        "verification_id": "vid-1",
    }
    return body, sig


def test_h63_n030_tamper_before_invoke_denies_and_demotes(trust_env):
    ks, _tmp = trust_env
    priv = _key(ks)
    body, sig = _signed_manifest(priv)
    adapter = Adapter(
        app_id="crm",
        app_name="crm",
        hwnd=1001,
        actions=[
            AdapterAction(
                name="click_ok",
                description="ok",
                automation_id="btnOk",
                control_type="Button",
                verification={"state": "VERIFIED", "verification_id": "vid-1"},
            )
        ],
        trust_level="publisher",
        signature=sig,
        publisher="Contoso",
    )
    _adapters["crm"] = adapter
    # Tamper capability after load / before invoke
    adapter.actions[0].automation_id = "EVIL"
    out = invoke_action("crm", "click_ok")
    assert out.get("ok") is False
    assert out.get("denied") is True
    assert out.get("code") == "TRUST_INSUFFICIENT"
    assert out.get("tampered") is True
    assert adapter.actions[0].verification["state"] == "INVALID"
    assert "verification_id" not in adapter.actions[0].verification


def test_h63_n030_reload_valid_signature_restores(trust_env):
    ks, tmp_path = trust_env
    priv = _key(ks)
    body, sig = _signed_manifest(priv)
    (tmp_path / "adapters" / "crm.json").write_text(json.dumps(body), encoding="utf-8")
    assert "crm" in load_persisted_adapters()["restored"]
    ad = _adapters["crm"]
    assert ad.trust_level == "publisher"
    # Tamper in memory then reload from good disk
    ad.actions[0].automation_id = "EVIL"
    _adapters.clear()
    assert "crm" in load_persisted_adapters()["restored"]
    ad2 = _adapters["crm"]
    assert ad2.actions[0].automation_id == "btnOk"
    assert ad2.trust_level == "publisher"
    # Bound + invoke path: revalidate allows (trust) — may still fail UI missing
    ad2.hwnd = 1001
    out = invoke_action("crm", "click_ok")
    assert out.get("code") != "TRUST_INSUFFICIENT"
    assert out.get("tampered") is not True


def test_h63_n030_disk_tamper_skips_load(trust_env):
    ks, tmp_path = trust_env
    priv = _key(ks)
    body, sig = _signed_manifest(priv)
    body["actions"][0]["automation_id"] = "TAMPERED"
    # keep old signature → resolve fails → claim publisher denied
    (tmp_path / "adapters" / "crm.json").write_text(json.dumps(body), encoding="utf-8")
    result = load_persisted_adapters()
    assert "crm" not in result["restored"]
    assert any(s["code"] == "TRUST_INSUFFICIENT" for s in result["skipped"])


def test_h63_n030_tamper_demotes_registry_verified_apis(trust_env):
    """N030: tamper withdraws VERIFIED registry records for the app."""
    import time

    from windows_os_api.apps.api_registry.model import (
        ApiStatus,
        issue_verification_proof,
        reset_api_registry,
    )
    from windows_os_api.api.rest.apis import _registry_openapi_document

    ks, _tmp = trust_env
    priv = _key(ks)
    body, _sig = _signed_manifest(priv)
    adapter = Adapter(
        app_id="crm",
        app_name="crm",
        hwnd=1001,
        actions=[
            AdapterAction(
                name="click_ok",
                description="ok",
                automation_id="btnOk",
                control_type="Button",
                params=[],
                risk="low",
                verification={"state": "VERIFIED", "verification_id": "vid-1"},
            )
        ],
        trust_level="publisher",
        signature=body["signature"],
        publisher="Contoso",
    )
    _adapters["crm"] = adapter

    reg = reset_api_registry()
    rec = reg.register(
        {
            "name": "click_ok",
            "method": "POST",
            "path": "/v1/apps/crm/actions/click_ok",
            "source": "virtual_adapter",
            "application_id": "crm",
            "capability": "crm.click_ok",
            "status": "VERIFIED",
            "verification_id": issue_verification_proof("vid-n030-reg"),
            "last_verified_at": time.time(),
            "permissions": [],
            "description": "x",
            "authentication_required": True,
        }
    )
    assert rec.status is ApiStatus.VERIFIED
    assert "/v1/apps/crm/actions/click_ok" in _registry_openapi_document(
        visible_app_ids=None
    )["paths"]

    adapter.actions[0].automation_id = "btnOk-TAMPERED"
    out = invoke_action("crm", "click_ok")
    assert out.get("ok") is False
    assert out.get("tampered") is True
    assert rec.id in (out.get("registry_demoted") or [])

    stored = reg.get(rec.id)
    assert stored is not None
    assert stored.status is ApiStatus.DISABLED
    assert stored.verification_id is None
    doc = _registry_openapi_document(visible_app_ids=None)
    assert "/v1/apps/crm/actions/click_ok" not in doc["paths"]
