"""Universal adapter engine."""
import os
os.environ.setdefault("WINOS_BACKEND", "fake")

from windows_os_api.backends.factory import reset_backend, get_backend
from windows_os_api.core.runtime.config import get_settings
from windows_os_api.apps.adapters.engine import create_adapter, invoke_action, reset_adapters
from windows_os_api.apps.semantic.mapper import map_intent_to_element
from windows_os_api.apps.ui_inspector.service import get_tree

def setup_function():
    get_settings.cache_clear()
    reset_backend()
    reset_adapters()

def test_create_adapter_from_crm_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(tmp_path))
    monkeypatch.setenv("WINOS_BACKEND", "fake")
    get_settings.cache_clear()
    reset_backend()
    adapter = create_adapter("contoso-crm", hwnd=1001)
    assert adapter.app_name == "Contoso CRM"
    names = [a.name for a in adapter.actions]
    assert any("save" in n for n in names)
    assert any("set_field" in n or "email" in n for n in names)
    assert adapter.openapi["openapi"].startswith("3.")
    assert len(adapter.openapi["paths"]) == len(adapter.actions)


def test_create_adapter_retries_an_incomplete_tree(tmp_path, monkeypatch):
    from windows_os_api.apps.adapters import engine

    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(tmp_path))
    monkeypatch.setenv("WINOS_BACKEND", "fake")
    get_settings.cache_clear()
    reset_backend()
    calls = 0

    def transient_tree(hwnd=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "name": "Contoso CRM",
                "control_type": "Window",
                "children": [],
            }
        return get_tree(hwnd)

    monkeypatch.setattr(engine, "get_tree", transient_tree)

    adapter = create_adapter("contoso-crm", hwnd=1001)

    assert calls == 2
    assert adapter.app_name == "Contoso CRM"
    assert adapter.actions

def test_invoke_set_and_click(tmp_path, monkeypatch):
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(tmp_path))
    monkeypatch.setenv("WINOS_BACKEND", "fake")
    get_settings.cache_clear(); reset_backend(); reset_adapters()
    create_adapter("contoso-crm")
    r = invoke_action("contoso-crm", "set_field_email", {"value": "a@b.it"})
    assert r["ok"] is True
    assert r["set_value"] == "a@b.it"
    # find save action
    from windows_os_api.apps.adapters.engine import get_adapter
    save = next(a for a in get_adapter("contoso-crm").actions if "save" in a.name)
    r2 = invoke_action("contoso-crm", save.name, {})
    assert r2["ok"] is True

def test_semantic_mapper_save():
    get_settings.cache_clear(); reset_backend()
    tree = get_tree(1001)
    el = map_intent_to_element(tree, "salva")
    assert el is not None
    assert el["automation_id"] == "btn.save"
