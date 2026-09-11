"""Fail-closed contracts for the packaged Windows service lifecycle."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE_SCRIPTS = ROOT / "installer" / "service_scripts"


def _script(name: str) -> str:
    return (SERVICE_SCRIPTS / name).read_text(encoding="utf-8")


def test_nssm_installer_uses_the_real_packaged_layout_and_key_file():
    script = _script("install_nssm.bat")

    assert r"%SCRIPT_DIR%..\winos-api.exe" in script
    assert r"%SCRIPT_DIR%winos-api.exe" in script
    assert "--api-key-file api_key.txt" in script
    assert "WINOS_SERVICE_PORT" in script
    assert "dev-key-change-me" not in script


def test_nssm_installer_fails_closed_and_configures_tree_shutdown():
    script = _script("install_nssm.bat")

    assert "where nssm.exe" not in script.lower()
    assert r"%SCRIPT_DIR%nssm.exe" in script
    assert "AppStopMethodSkip" in script
    assert "AppStopMethodConsole" in script
    assert "AppKillProcessTree" in script
    assert "AppStdout" in script
    assert "AppStderr" in script
    assert "Invoke-RestMethod" in script
    assert "goto :rollback" in script
    assert "exit /b 1" in script


def test_direct_sc_installer_refuses_the_unsupported_console_registration():
    script = _script("install_sc.bat").lower()

    assert "sc create" not in script
    assert "nssm" in script
    assert "exit /b 1" in script


def test_uninstaller_waits_for_stop_and_deletes_the_service():
    script = _script("uninstall_service.bat").lower()

    assert "sc.exe stop" in script
    assert "sc.exe query" in script
    assert "sc.exe delete" in script
    assert "servicecontrollerstatus]::stopped" in script
    assert "exit /b 1" in script


def test_generated_scripts_match_the_packaged_scripts(tmp_path):
    from windows_os_api.installer.service import generate_install_scripts

    generated = generate_install_scripts(tmp_path)
    assert set(generated) == {
        "install_nssm.bat",
        "install_sc.bat",
        "uninstall_service.bat",
    }
    for name in generated:
        assert (tmp_path / name).read_text(encoding="utf-8") == _script(name)


def test_api_key_file_loader_accepts_one_nonempty_line(tmp_path):
    from windows_os_api.cli.main import load_api_key_file

    key_file = tmp_path / "api_key.txt"
    # Bytes preserve one literal CRLF on every host. Text-mode write would turn
    # this into CRCRLF on Windows and test Python's newline translation instead.
    key_file.write_bytes(b"service-secret\r\n")
    assert load_api_key_file(key_file) == "service-secret"


@pytest.mark.parametrize("body", ["", "  \r\n", "first\nsecond\n"])
def test_api_key_file_loader_rejects_ambiguous_or_empty_content(tmp_path, body):
    from windows_os_api.cli.main import load_api_key_file

    key_file = tmp_path / "api_key.txt"
    key_file.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        load_api_key_file(key_file)


def test_api_key_file_loader_rejects_missing_file(tmp_path):
    from windows_os_api.cli.main import load_api_key_file

    with pytest.raises(OSError):
        load_api_key_file(tmp_path / "missing.txt")


def test_serve_wires_the_key_file_into_authenticated_settings(tmp_path, monkeypatch):
    import uvicorn

    from windows_os_api.cli.main import main

    key_file = tmp_path / "api_key.txt"
    key_file.write_text("service-secret\n", encoding="utf-8")
    monkeypatch.setenv("WINOS_BACKEND", "fake")
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(tmp_path / "sandbox"))
    monkeypatch.setenv("WINOS_REQUIRE_AUTH", "false")
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)

    assert main(["serve", "--api-key-file", str(key_file)]) == 0
    assert json.loads(os.environ["WINOS_API_KEYS"]) == ["service-secret"]
    assert os.environ["WINOS_REQUIRE_AUTH"] == "true"


def test_runtime_records_a_graceful_shutdown_for_service_evidence(tmp_sandbox):
    """A forced process kill cannot produce the shutdown evidence used by the hard smoke."""
    from fastapi.testclient import TestClient

    from windows_os_api.core.runtime.app import create_app
    from windows_os_api.core.security.audit import get_audit_logger

    with TestClient(create_app()):
        pass

    actions = [entry["action"] for entry in get_audit_logger().read_all()]
    assert actions[-2:] == ["server.startup", "server.shutdown"]


def test_hard_smoke_covers_scm_http_process_port_and_shutdown_evidence():
    script = (ROOT / "scripts" / "windows_service_smoke.py").read_text(encoding="utf-8")

    for evidence in (
        'invoke_batch(install_script, "service install"',
        '["sc.exe", "stop", SERVICE_NAME]',
        '["sc.exe", "start", SERVICE_NAME]',
        'invoke_batch(uninstall_script, "service uninstall"',
        'http_get(port, "/v1/system", api_key)',
        "not winos_processes()",
        "not port_is_open(port)",
        '"server.shutdown"',
    ):
        assert evidence in script


def test_windows_workflows_stage_the_native_nssm_binary_not_the_chocolatey_shim():
    for name in ("build.yml", "release.yml"):
        workflow = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "2.24.101.20180116" in workflow
        assert "win64" in workflow
        assert "service_scripts\\nssm.exe" in workflow
        assert "refusing Chocolatey shim" in workflow
