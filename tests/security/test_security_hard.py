"""Security tests: injection, path traversal, authz."""
import pytest

@pytest.mark.security
def test_path_traversal_fs(client, auth_headers):
    for payload in ["../etc/passwd", "..\\..\\Windows\\System32\\config\\SAM", "/etc/shadow"]:
        r = client.get("/v1/fs/read", headers=auth_headers, params={"path": payload})
        assert r.status_code == 403, payload

@pytest.mark.security
def test_path_traversal_write(client, auth_headers):
    r = client.put("/v1/fs", headers=auth_headers, json={"path": "../evil.txt", "content": "x"})
    assert r.status_code == 403

@pytest.mark.security
def test_terminal_injection_blocked(client, auth_headers):
    for cmd in ["echo a && del /f", "dir | notepad", "x`id`", "a$(reboot)", "line1\nline2"]:
        r = client.post("/v1/terminal/execute", headers=auth_headers, json={"command": cmd, "policy": "ALLOW"})
        body = r.json()
        assert body.get("ok") is False

@pytest.mark.security
def test_missing_permission_forbidden(client):
    # no key
    assert client.get("/v1/processes").status_code == 401

@pytest.mark.security
def test_viewer_cannot_use_admin_key_for_power_as_operator(client, auth_headers):
    # operator cannot call power (needs ADMIN)
    r = client.post("/v1/system/power/lock", headers=auth_headers)
    assert r.status_code == 403

@pytest.mark.security
def test_openapi_available(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "/v1/health" in r.json()["paths"]


@pytest.mark.security
def test_audit_logs_terminal_denial(client, auth_headers, tmp_sandbox):
    from windows_os_api.core.security.audit import get_audit_logger

    r = client.post(
        "/v1/terminal/execute",
        headers=auth_headers,
        json={"command": "echo hi && reboot", "policy": "ALLOW"},
    )
    assert r.json().get("ok") is False
    # Route still audits the attempt
    entries = get_audit_logger().read_all()
    assert any(e.get("action") == "terminal.execute" for e in entries)


@pytest.mark.security
def test_viewer_cannot_control_services(client):
    # no auth
    assert client.post("/v1/services/foo", json={"action": "start"}).status_code == 401


@pytest.mark.security
def test_capabilities_expose_security_related_flags(client, auth_headers):
    r = client.get("/v1/capabilities", headers=auth_headers)
    assert r.status_code == 200
    flags = r.json().get("feature_flags") or {}
    # Fake backend should report honest privileged=false
    assert flags.get("privileged") is False
