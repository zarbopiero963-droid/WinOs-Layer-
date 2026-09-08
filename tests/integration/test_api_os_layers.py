"""Integration: windows, ui, fs, network, services, etc."""
def test_windows_and_ui(client, auth_headers):
    wins = client.get("/v1/windows", headers=auth_headers).json()["windows"]
    assert any(w["hwnd"] == 1001 for w in wins)
    tree = client.get("/v1/ui/tree", headers=auth_headers).json()
    assert tree["name"] == "Contoso CRM"
    focus = client.post("/v1/windows/1001/focus", headers=auth_headers)
    assert focus.json()["ok"] is True

def test_input_clipboard_display(client, auth_headers):
    assert client.post("/v1/input/mouse/move", headers=auth_headers, json={"x": 10, "y": 20}).json()["ok"]
    assert client.post("/v1/input/mouse/click", headers=auth_headers, json={"x": 10, "y": 20}).json()["ok"]
    assert client.post("/v1/input/keyboard/type", headers=auth_headers, json={"text": "hi"}).json()["ok"]
    client.put("/v1/clipboard", headers=auth_headers, json={"text": "clip"})
    assert client.get("/v1/clipboard", headers=auth_headers).json()["text"] == "clip"
    disp = client.get("/v1/displays", headers=auth_headers).json()["displays"]
    assert len(disp) >= 1
    shot = client.get("/v1/displays/screenshot", headers=auth_headers).json()
    assert shot["ok"] and "data_base64" in shot

def test_filesystem_sandbox(client, auth_headers):
    w = client.put("/v1/fs", headers=auth_headers, json={"path": "note.txt", "content": "hello"})
    assert w.status_code == 200
    r = client.get("/v1/fs/read", headers=auth_headers, params={"path": "note.txt"})
    assert r.json()["text"] == "hello"
    listing = client.get("/v1/fs", headers=auth_headers, params={"path": "."}).json()["entries"]
    assert any(e["name"] == "note.txt" for e in listing)
    # path traversal blocked
    bad = client.get("/v1/fs/read", headers=auth_headers, params={"path": "../etc/passwd"})
    assert bad.status_code == 403

def test_storage_network_services(client, auth_headers, monkeypatch):
    assert len(client.get("/v1/storage/drives", headers=auth_headers).json()["drives"]) >= 1
    assert len(client.get("/v1/network/interfaces", headers=auth_headers).json()["interfaces"]) >= 1
    svcs = client.get("/v1/services", headers=auth_headers).json()["services"]
    assert any(s["name"] == "WinOsApi" for s in svcs)

    # Il controllo dei servizi e' default-deny (decisione owner D1-B, issue #6):
    # questa chiamata prima rispondeva `ok: true` senza che nessuno avesse
    # autorizzato `FakeSvc`. Ora serve l'allowlist, e la sua assenza e' un 403.
    refused = client.post("/v1/services/FakeSvc", headers=auth_headers, json={"action": "start"})
    assert refused.status_code == 403, refused.text

    monkeypatch.setenv("WINOS_SERVICE_ALLOWLIST", "FakeSvc")
    ctrl = client.post("/v1/services/FakeSvc", headers=auth_headers, json={"action": "start"})
    assert ctrl.json()["ok"] is True, ctrl.text

def test_audio_devices_printers_users(client, auth_headers):
    assert client.get("/v1/audio/devices", headers=auth_headers).status_code == 200
    assert client.get("/v1/devices", headers=auth_headers).status_code == 200
    assert client.get("/v1/printers", headers=auth_headers).status_code == 200
    assert len(client.get("/v1/users", headers=auth_headers).json()["users"]) >= 1
    assert len(client.get("/v1/sessions", headers=auth_headers).json()["sessions"]) >= 1

def test_registry_and_terminal(client, auth_headers, admin_headers):
    r = client.get("/v1/registry", headers=auth_headers, params={"path": r"HKCU\Software\ContosoCRM", "name": "Theme"})
    assert r.json()["ok"] is True
    # Only registered commands run, and they run without a shell.
    blocked = client.post("/v1/terminal/execute", headers=auth_headers, json={"command": "echo hi; rm -rf /", "policy": "ALLOW"})
    assert blocked.json()["ok"] is False
    allowed = client.post("/v1/terminal/execute", headers=auth_headers, json={"command": "whoami", "policy": "ALLOW"})
    assert allowed.json()["ok"] is True
    deny = client.post("/v1/terminal/execute", headers=auth_headers, json={"command": "x", "policy": "DENY"})
    assert deny.json()["policy"] == "DENY"
    # ADMIN policy requires admin role
    assert client.post("/v1/terminal/execute", headers=auth_headers, json={"command": "x", "policy": "ADMIN"}).status_code == 403
    assert client.post("/v1/terminal/execute", headers=admin_headers, json={"command": "whoami", "policy": "ADMIN"}).json()["ok"] is True
    # This line used to assert that an admin could run the arbitrary command "x".
    # That is the behaviour the allowlist removes, so the guarantee that replaced
    # it gets asserted explicitly instead of quietly disappearing.
    arbitrary = client.post("/v1/terminal/execute", headers=admin_headers, json={"command": "x", "policy": "ADMIN"})
    assert arbitrary.json()["ok"] is False
