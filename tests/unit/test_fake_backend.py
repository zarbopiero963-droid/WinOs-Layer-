"""FakeBackend hard unit tests."""
from windows_os_api.backends.fake import FakeBackend, CRM_UI_TREE

def test_system_info(tmp_path):
    b = FakeBackend(str(tmp_path))
    info = b.get_system_info()
    assert info["backend"] == "fake"
    assert info["os"] == "Windows"
    assert "hostname" in info

def test_processes_crud(tmp_path):
    b = FakeBackend(str(tmp_path))
    procs = b.list_processes()
    assert any(p["name"] == "ContosoCRM.exe" for p in procs)
    started = b.start_process("demo.exe", ["--flag"])
    assert started["pid"] > 0
    assert b.get_process(started["pid"])["status"] == "running"
    term = b.terminate_process(started["pid"])
    assert term["ok"] is True
    assert b.get_process(started["pid"])["status"] == "terminated"

def test_discover_apps_realistic(tmp_path):
    apps = FakeBackend(str(tmp_path)).discover_apps()
    assert len(apps) >= 3
    assert any(a["id"] == "contoso-crm" for a in apps)

def test_crm_ui_tree(tmp_path):
    tree = FakeBackend(str(tmp_path)).get_ui_tree(1001)
    assert tree["name"] == "Contoso CRM"
    assert tree["automation_id"] == CRM_UI_TREE["automation_id"]
    ids = []
    def walk(n):
        if n.get("automation_id"):
            ids.append(n["automation_id"])
        for c in n.get("children") or []:
            walk(c)
    walk(tree)
    assert "btn.save" in ids
    assert "field.email" in ids

def test_clipboard_roundtrip(tmp_path):
    b = FakeBackend(str(tmp_path))
    assert b.clipboard_set("ciao")["ok"]
    assert b.clipboard_get()["text"] == "ciao"

def test_fs_sandbox_blocks_escape(tmp_path):
    b = FakeBackend(str(tmp_path / "sb"))
    b.fs_write("ok.txt", "hello")
    assert b.fs_read("ok.txt")["text"] == "hello"
    try:
        b.fs_read("../outside.txt")
        assert False, "should have raised"
    except PermissionError:
        pass

def test_terminal_policies(tmp_path):
    b = FakeBackend(str(tmp_path))
    assert b.terminal_execute("dir", "ALLOW")["ok"] is True
    deny = b.terminal_execute("rm -rf /", "DENY")
    assert deny["ok"] is False
    assert deny["policy"] == "DENY"
    admin = b.terminal_execute("whoami", "ADMIN")
    assert admin["ok"] is True
    assert "[admin]" in admin["stdout"]

def test_registry(tmp_path):
    b = FakeBackend(str(tmp_path))
    r = b.registry_read(r"HKCU\Software\ContosoCRM", "Theme")
    assert r["ok"] and r["value"] == "dark"
    b.registry_write(r"HKCU\Software\ContosoCRM", "Theme", "light")
    assert b.registry_read(r"HKCU\Software\ContosoCRM", "Theme")["value"] == "light"
