"""H63-N029 — Trust adapter production e identità publisher (R48 W097 L097 G09 G20)."""
from __future__ import annotations

from pathlib import Path

import pytest

from windows_os_api.apps.adapters import store
from windows_os_api.apps.adapters.engine import (
    Adapter,
    AdapterAction,
    _adapters,
    invoke_action,
    load_persisted_adapters,
    rebind_adapter,
)
from windows_os_api.apps.trust.keystore import (
    TrustKeystore,
    generate_ed25519_keypair,
    reset_keystore_for_tests,
)
from windows_os_api.apps.trust.signing import (
    resolve_trust_level,
    sign_adapter_manifest,
    sign_adapter_manifest_ed25519,
    verify_adapter_signature,
)


@pytest.fixture
def trust_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store_path = tmp_path / "keystore.json"
    ks = TrustKeystore(path=store_path)
    reset_keystore_for_tests(ks)
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(tmp_path / "adapters"))
    (tmp_path / "adapters").mkdir()
    _adapters.clear()
    yield ks
    _adapters.clear()
    reset_keystore_for_tests(TrustKeystore(path=tmp_path / "empty.json"))


def _register_key(ks: TrustKeystore, key_id: str, publisher: str):
    priv, pub = generate_ed25519_keypair()
    ks.add_public_key(key_id=key_id, publisher=publisher, public_key_raw=pub, persist=True)
    return priv


def test_h63_n029_good_ed25519_publisher(trust_env):
    ks = trust_env
    priv = _register_key(ks, "pub-1", "Contoso")
    manifest = {"app_id": "crm", "version": "1", "publisher": "Contoso"}
    sig = sign_adapter_manifest_ed25519(manifest, key_id="pub-1", private_key=priv)
    assert verify_adapter_signature(manifest, sig) is True
    assert resolve_trust_level(manifest, sig) == "publisher"


def test_h63_n029_bad_signature(trust_env):
    ks = trust_env
    priv = _register_key(ks, "pub-1", "Contoso")
    manifest = {"app_id": "crm", "publisher": "Contoso"}
    sig = sign_adapter_manifest_ed25519(manifest, key_id="pub-1", private_key=priv)
    tampered = dict(manifest, version="9")
    assert verify_adapter_signature(tampered, sig) is False
    assert resolve_trust_level(tampered, sig) == "unsigned"


def test_h63_n029_revoked_key(trust_env):
    ks = trust_env
    priv = _register_key(ks, "pub-1", "Contoso")
    manifest = {"app_id": "crm", "publisher": "Contoso"}
    sig = sign_adapter_manifest_ed25519(manifest, key_id="pub-1", private_key=priv)
    assert resolve_trust_level(manifest, sig) == "publisher"
    ks.revoke("pub-1", persist=True)
    assert verify_adapter_signature(manifest, sig) is False
    assert resolve_trust_level(manifest, sig) == "unsigned"


def test_h63_n029_forged_publisher(trust_env):
    ks = trust_env
    priv = _register_key(ks, "pub-1", "Contoso")
    # Attacker puts FakeCorp in manifest while key is bound to Contoso
    manifest = {"app_id": "crm", "publisher": "FakeCorp"}
    sig = sign_adapter_manifest_ed25519(manifest, key_id="pub-1", private_key=priv)
    assert verify_adapter_signature(manifest, sig) is False
    assert resolve_trust_level(manifest, sig) == "unsigned"


def test_h63_n029_hmac_never_production(trust_env):
    manifest = {
        "app_id": "crm",
        "publisher": "Contoso",
        "verified_publisher": True,  # self-asserted — must be ignored
    }
    sig = sign_adapter_manifest(manifest)
    assert resolve_trust_level(manifest, sig) == "dev"


def test_h63_n029_load_denied_for_forged_claim(trust_env, tmp_path: Path):
    # Persist a manifest claiming publisher with only HMAC
    adapters = Path(tmp_path / "adapters")
    manifest = {
        "manifest_version": 1,
        "app_id": "evil-app",
        "app_name": "evil",
        "trust_level": "publisher",
        "publisher": "Contoso",
        "signature": sign_adapter_manifest({"app_id": "evil-app", "publisher": "Contoso"}),
        "actions": [
            {
                "name": "click_x",
                "description": "x",
                "automation_id": "x",
                "control_type": "Button",
                "params": [],
                "risk": "low",
            }
        ],
    }
    # detect MANIFEST_VERSION
    from windows_os_api.apps.adapters.store import MANIFEST_VERSION

    manifest["manifest_version"] = MANIFEST_VERSION
    (adapters / "evil-app.json").write_text(__import__("json").dumps(manifest), encoding="utf-8")
    result = load_persisted_adapters()
    assert "evil-app" not in result["restored"]
    assert any(s["code"] == "TRUST_INSUFFICIENT" for s in result["skipped"])


def test_h63_n029_execute_denied_when_trust_insufficient(trust_env):
    # In-memory adapter claiming verified with bad/revoked ed25519
    ks = trust_env
    priv = _register_key(ks, "pub-1", "Contoso")
    manifest = {"app_id": "crm", "app_name": "crm", "publisher": "Contoso"}
    sig = sign_adapter_manifest_ed25519(manifest, key_id="pub-1", private_key=priv)
    ks.revoke("pub-1", persist=True)

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
            )
        ],
        trust_level="publisher",
        signature=sig,
        publisher="Contoso",
    )
    _adapters["crm"] = adapter
    out = invoke_action("crm", "click_ok")
    assert out.get("ok") is False
    assert out.get("denied") is True
    assert out.get("code") == "TRUST_INSUFFICIENT"


def test_h63_n029_good_load_and_level(trust_env, tmp_path: Path):
    ks = trust_env
    priv = _register_key(ks, "pub-1", "Contoso")
    from windows_os_api.apps.adapters.store import MANIFEST_VERSION
    import json

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "app_id": "crm",
        "app_name": "crm",
        "publisher": "Contoso",
        "actions": [],
    }
    sig = sign_adapter_manifest_ed25519(manifest, key_id="pub-1", private_key=priv)
    manifest["trust_level"] = "publisher"
    manifest["signature"] = sig
    (tmp_path / "adapters" / "crm.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = load_persisted_adapters()
    assert "crm" in result["restored"], result
    assert _adapters["crm"].trust_level == "publisher"
