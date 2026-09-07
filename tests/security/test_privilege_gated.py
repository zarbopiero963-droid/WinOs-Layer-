"""Security: privileged ops gated; path traversal & shell still blocked."""
from __future__ import annotations

import pytest

from windows_os_api.core.security.audit import get_audit_logger, reset_audit_logger


@pytest.mark.security
def test_elevate_denied_without_admin_flag(client, auth_headers, tmp_sandbox, monkeypatch):
    monkeypatch.delenv("WINOS_ALLOW_PRIVILEGED", raising=False)
    monkeypatch.setenv("WINOS_ALLOW_PRIVILEGED", "false")
    # automator key is not admin
    r = client.post(
        "/v1/privilege/elevate",
        headers=auth_headers,
        json={"argv": ["/bin/true"]},
    )
    assert r.status_code == 403  # missing ADMIN permission on route


@pytest.mark.security
def test_elevate_denied_admin_without_env_flag(client, admin_headers, monkeypatch):
    monkeypatch.setenv("WINOS_ALLOW_PRIVILEGED", "false")
    from windows_os_api.core.runtime.config import get_settings

    get_settings.cache_clear()
    r = client.post(
        "/v1/privilege/elevate",
        headers=admin_headers,
        json={"argv": ["/bin/true"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False
    assert body.get("denied") is True
    assert body.get("code") == "flag_disabled"
    # Audit denial
    entries = get_audit_logger().read_all()
    assert any(e.get("action", "").startswith("privilege") and e.get("outcome") == "denied" for e in entries)


@pytest.mark.security
def test_elevate_unit_helper_requires_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("WINOS_ALLOW_PRIVILEGED", "true")
    monkeypatch.setenv("WINOS_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    from windows_os_api.core.runtime.config import get_settings
    from windows_os_api.core.security.privilege import attempt_elevation

    get_settings.cache_clear()
    reset_audit_logger()
    denied = attempt_elevation(["/bin/true"], auth_has_admin=False, subject="t")
    assert denied["denied"] is True
    assert denied["code"] == "missing_admin"


@pytest.mark.security
def test_path_traversal_still_blocked(client, auth_headers):
    for payload in ["../etc/passwd", "/etc/shadow", "..\\..\\Windows\\System32"]:
        r = client.get("/v1/fs/read", headers=auth_headers, params={"path": payload})
        assert r.status_code == 403, payload


@pytest.mark.security
def test_arbitrary_shell_still_denied(client, auth_headers):
    for cmd in ["echo a && reboot", "id; cat /etc/shadow", "x$(reboot)", "a|b"]:
        r = client.post(
            "/v1/terminal/execute",
            headers=auth_headers,
            json={"command": cmd, "policy": "ALLOW"},
        )
        body = r.json()
        assert body.get("ok") is False


@pytest.mark.security
def test_service_name_injection_blocked_at_helper():
    from windows_os_api.backends.linux_services import control_service

    r = control_service("../../evil", "start", run=lambda *a, **k: None)
    assert r.get("ok") is False
    assert r.get("denied") is True


@pytest.mark.security
def test_power_structured_denial_on_linux():
    import sys

    if sys.platform == "win32":
        pytest.skip("linux")
    from windows_os_api.backends.linux import LinuxBackend
    import tempfile
    from pathlib import Path

    b = LinuxBackend(sandbox_root=tempfile.mkdtemp())
    r = b.power_action("reboot")
    assert r.get("ok") is False
    assert r.get("denied") is True
    assert r.get("code") == "hardware_protected"


@pytest.mark.security
def test_session_endpoint(client, auth_headers):
    r = client.get("/v1/session", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "sessions" in body or "session" in body
