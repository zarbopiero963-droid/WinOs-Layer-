"""UI Automation hard tests + import smoke for Windows UIA module."""
from __future__ import annotations

import sys

import pytest

pytestmark = [pytest.mark.windows]


def test_uia_module_import_smoke():
    """Compile/import smoke — runs on Linux too for the windows-marked suite filter.

    On Linux CI we collect with -m 'not windows', so this is skipped there.
    When someone runs the full suite on Linux, skipif below still applies for
    runtime UIA; this smoke stays importable always but is marked windows so
    Linux `not windows` stays green without collecting it.
    """
    from windows_os_api.apps.ui_inspector import uia_windows

    info = uia_windows.describe_backend()
    assert "platform" in info
    assert "modules" in info
    assert info["uia_available"] is (sys.platform == "win32" and bool(info["modules"]))


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows (win32)")
@pytest.mark.requires_display
def test_uia_notepad_or_skip():
    from windows_os_api.apps.ui_inspector import uia_windows

    if not uia_windows.uia_available():
        pytest.skip("uiautomation/comtypes/pywinauto not installed")
    try:
        tree = uia_windows.get_notepad_tree()
    except RuntimeError as e:
        pytest.skip(str(e))
    assert tree.get("control_type") == "Window"
    assert "Notepad" in (tree.get("name") or "") or tree.get("class_name") == "Notepad"
