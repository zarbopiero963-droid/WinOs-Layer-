"""Portable import/compile smoke for Windows UIA helper module."""
from windows_os_api.apps.ui_inspector import uia_windows


def test_uia_windows_module_importable():
    info = uia_windows.describe_backend()
    assert isinstance(info["modules"], list)
    assert "platform" in info
    assert uia_windows.uia_available() in (True, False)
