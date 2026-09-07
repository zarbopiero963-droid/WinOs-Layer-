"""Integration: health, system, security, processes, apps."""
def test_health_no_auth_required(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "version" in r.json()

def test_system_requires_auth(client):
    assert client.get("/v1/system").status_code == 401
    r = client.get("/v1/system", headers={"X-API-Key": "dev-key-change-me"})
    assert r.status_code == 200
    body = r.json()
    assert body["backend"] == "fake"
    assert body["os"] == "Windows"

def test_capabilities(client, auth_headers):
    r = client.get("/v1/capabilities", headers=auth_headers)
    assert r.status_code == 200
    feats = r.json()["features"]
    assert "adapters" in feats
    assert "mcp" in feats

def test_resources_uptime(client, auth_headers):
    assert client.get("/v1/system/resources", headers=auth_headers).status_code == 200
    up = client.get("/v1/system/uptime", headers=auth_headers).json()
    assert up["uptime_seconds"] >= 0

def test_invalid_api_key(client):
    r = client.get("/v1/system", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401

def test_processes(client, auth_headers):
    r = client.get("/v1/processes", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()["processes"]) >= 1
    started = client.post("/v1/processes", headers=auth_headers, json={"command": "tool.exe", "args": ["a"]})
    assert started.status_code == 200
    pid = started.json()["pid"]
    got = client.get(f"/v1/processes/{pid}", headers=auth_headers)
    assert got.status_code == 200
    term = client.delete(f"/v1/processes/{pid}", headers=auth_headers)
    assert term.json()["ok"] is True

def test_auth_me_and_admin_audit(client, auth_headers, admin_headers):
    me = client.get("/v1/auth/me", headers=auth_headers).json()
    assert me["role"] == "automator"
    assert client.get("/v1/audit", headers=auth_headers).status_code == 403
    assert client.get("/v1/audit", headers=admin_headers).status_code == 200

def test_remote_policy_localhost(client, auth_headers):
    r = client.get("/v1/remote/policy", headers=auth_headers).json()
    assert r["remote_access_enabled"] is False
    assert r["host"] == "127.0.0.1"
