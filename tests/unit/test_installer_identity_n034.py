"""H63-N034 — Installer Windows: identity, key ACL, least-privilege, single-instance wizard."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ISS = ROOT / "installer" / "inno" / "winos-api.iss"
SERVICE_SCRIPTS = ROOT / "installer" / "service_scripts"


def _iss() -> str:
    return ISS.read_text(encoding="utf-8")


def _nssm() -> str:
    return (SERVICE_SCRIPTS / "install_nssm.bat").read_text(encoding="utf-8")


def test_h63_n034_identity_constants():
    from windows_os_api.installer.identity import (
        APP_MUTEX,
        KEY_ACL_READ_PRINCIPALS,
        SERVICE_ACCOUNT,
        SETUP_MUTEX,
        WEAK_API_KEYS,
        assert_release_api_key,
        is_weak_api_key,
    )

    assert SERVICE_ACCOUNT == r"NT AUTHORITY\LocalService"
    assert SETUP_MUTEX == "WinOsApiSetupMutex"
    assert APP_MUTEX == "WinOsApiAppMutex"
    assert SERVICE_ACCOUNT in KEY_ACL_READ_PRINCIPALS
    assert r"NT AUTHORITY\SYSTEM" in KEY_ACL_READ_PRINCIPALS
    assert r"BUILTIN\Administrators" in KEY_ACL_READ_PRINCIPALS
    assert "dev-key-change-me" in WEAK_API_KEYS
    assert is_weak_api_key("Dev-Key-Change-Me")
    assert assert_release_api_key("a" * 32) == "a" * 32
    with pytest.raises(ValueError, match="denylist"):
        assert_release_api_key("dev-key-change-me")
    with pytest.raises(ValueError):
        assert_release_api_key("   ")


def test_h63_n034_iss_single_instance_mutex():
    text = _iss()
    assert "SetupMutex=WinOsApiSetupMutex" in text or "SetupMutex={#MySetupMutex}" in text
    assert "WinOsApiSetupMutex" in text
    assert "WinOsApiAppMutex" in text
    assert "AppMutex=" in text or "AppMutex={#" in text


def test_h63_n034_iss_csprng_key_and_acl_via_helper():
    text = _iss()
    assert "write_secure_api_key.ps1" in text
    assert "GenerateApiKey" not in text  # weak Inno Random() path removed
    assert "Random(" not in text
    helper = (SERVICE_SCRIPTS / "write_secure_api_key.ps1").read_text(encoding="utf-8")
    assert "RandomNumberGenerator" in helper
    assert "SetAccessRuleProtection" in helper
    assert "NT AUTHORITY\\LOCAL SERVICE" in helper or "LOCAL SERVICE" in helper
    assert "BUILTIN\\Administrators" in helper
    assert "NT AUTHORITY\\SYSTEM" in helper


def test_h63_n034_nssm_least_privilege_objectname_and_workdir():
    script = _nssm()
    assert "ObjectName" in script
    assert r"NT AUTHORITY\LocalService" in script
    assert "AppDirectory" in script
    assert "harden_service_dirs.ps1" in script
    harden = (SERVICE_SCRIPTS / "harden_service_dirs.ps1").read_text(encoding="utf-8")
    assert "LOCAL SERVICE" in harden
    assert "Modify" in harden


def test_h63_n034_nssm_rejects_weak_dev_keys():
    script = _nssm()
    assert "dev-key-change-me" in script
    assert "denylist" in script.lower() or "weak" in script.lower()


def test_h63_n034_service_manifest_exposes_least_privilege_account():
    from windows_os_api.installer.service import service_manifest

    m = service_manifest()
    assert m["account"] == r"NT AUTHORITY\LocalService"
    assert m["least_privilege"] is True


def test_h63_n034_generated_scripts_match_packaged(tmp_path):
    from windows_os_api.installer.service import generate_install_scripts

    generated = generate_install_scripts(tmp_path)
    for name in generated:
        packaged = (SERVICE_SCRIPTS / name).read_text(encoding="utf-8")
        assert (tmp_path / name).read_text(encoding="utf-8") == packaged


def test_h63_n034_validate_requires_identity_contracts():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_installer_n034", ROOT / "scripts" / "build_installer.py"
    )
    assert spec and spec.loader
    bi_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bi_mod)
    result = bi_mod.validate(verbose=False)
    assert result["ok"] is True
    for key in (
        "iss_setup_mutex",
        "iss_secure_key_script",
        "nssm_objectname_localservice",
        "nssm_harden_dirs",
        "nssm_weak_key_deny",
        "helper_write_secure_api_key.ps1",
        "helper_harden_service_dirs.ps1",
    ):
        assert result["checks"].get(key) is True, key


def test_h63_n034_preserve_fix54_api_key_file_and_localhost():
    """#54 contracts must remain: --api-key-file, localhost bind, no secret on cmdline."""
    script = _nssm()
    assert "--api-key-file api_key.txt" in script
    assert "127.0.0.1" in script
    assert "WINOS_API_KEYS=" not in script  # secret not baked into service env
    assert "AppKillProcessTree" in script
    assert "AppStopMethodConsole" in script
