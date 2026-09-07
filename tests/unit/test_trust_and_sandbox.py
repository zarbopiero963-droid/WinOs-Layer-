"""Signed trust + sandbox permissions."""
from windows_os_api.apps.trust.signing import sign_adapter_manifest, verify_adapter_signature, resolve_trust_level, trust_allows
from windows_os_api.apps.sandbox.permissions import SandboxPolicy, set_policy, check_action, reset_policies

def test_sign_verify():
    manifest = {"app_id": "contoso-crm", "version": "1.0.0", "publisher": "Contoso"}
    sig = sign_adapter_manifest(manifest)
    assert verify_adapter_signature(manifest, sig)
    assert not verify_adapter_signature(manifest, "deadbeef")
    assert resolve_trust_level(manifest, sig) == "verified"
    assert resolve_trust_level(manifest, None) == "unsigned"
    assert trust_allows("verified", "dev")
    assert not trust_allows("unsigned", "verified")

def test_sandbox_deny():
    reset_policies()
    set_policy(SandboxPolicy(app_id="contoso-crm", denied_actions={"click_btn_save"}, max_risk="low"))
    assert check_action("contoso-crm", "click_btn_save", "low")["allowed"] is False
    assert check_action("contoso-crm", "set_field_email", "high")["allowed"] is False
    assert check_action("contoso-crm", "set_field_email", "low")["allowed"] is True
