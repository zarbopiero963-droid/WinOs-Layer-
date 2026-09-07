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
