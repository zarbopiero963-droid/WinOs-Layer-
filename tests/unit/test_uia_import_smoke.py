"""Portable import/compile smoke for Windows UIA helper module."""
import sys
import types

from windows_os_api.apps.ui_inspector import uia_windows


def test_uia_windows_module_importable():
    info = uia_windows.describe_backend()
    assert isinstance(info["modules"], list)
    assert "platform" in info
    assert uia_windows.uia_available() in (True, False)


def test_build_tree_falls_back_from_an_incomplete_provider(monkeypatch):
    incomplete = {"name": "Notepad", "children": [], "source": "uiautomation"}
    complete = {
        "name": "Notepad",
        "children": [{"control_type": "Document"}],
        "source": "comtypes",
    }
    calls = []

    monkeypatch.setattr(uia_windows.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "uiautomation", types.ModuleType("uiautomation"))
    monkeypatch.setitem(sys.modules, "comtypes", types.ModuleType("comtypes"))
    monkeypatch.setattr(
        uia_windows,
        "_tree_uiautomation",
        lambda *args, **kwargs: calls.append("uiautomation") or incomplete,
    )
    monkeypatch.setattr(
        uia_windows,
        "_tree_comtypes",
        lambda *args, **kwargs: calls.append("comtypes") or complete,
    )

    tree = uia_windows.build_tree(hwnd=123)

    assert tree is complete
    assert calls == ["uiautomation", "comtypes"]
