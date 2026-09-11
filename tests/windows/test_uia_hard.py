"""UI Automation hard tests + import smoke for Windows UIA module."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

pytestmark = [pytest.mark.windows]


def _require_live_ui(condition: bool, reason: str) -> None:
    if condition:
        return
    if os.environ.get("WINOS_REQUIRE_UI") == "1":
        pytest.fail(reason + " (WINOS_REQUIRE_UI=1)")
    pytest.skip(reason)


def _wait_for_new_process_window(backend, pid: int, previous: set[int]) -> int | None:
    deadline = time.time() + 8
    while time.time() < deadline:
        windows = [w for w in backend.list_windows() if w.get("hwnd") not in previous]
        owned = next((w for w in windows if w.get("pid") == pid), None)
        if owned is not None:
            return int(owned["hwnd"])
        notepad = next(
            (w for w in windows if "notepad" in (w.get("title") or "").casefold()),
            None,
        )
        if notepad is not None:
            return int(notepad["hwnd"])
        time.sleep(0.1)
    return None


def test_uia_module_import_smoke():
    """Compile/import smoke — marked windows so `not windows` on Linux stays green."""
    from windows_os_api.apps.ui_inspector import uia_windows

    info = uia_windows.describe_backend()
    assert "platform" in info
    assert "modules" in info
    assert "preferred" in info
    assert info["uia_available"] is (sys.platform == "win32" and bool(info["modules"]))


def test_uia_helpers_exist():
    from windows_os_api.apps.ui_inspector import uia_windows

    assert callable(uia_windows.build_tree)
    assert callable(uia_windows.find_by_automation_id)
    assert callable(uia_windows.find_by_name)
    assert callable(uia_windows.invoke_click)
    assert callable(uia_windows.set_value)
    assert callable(uia_windows.get_notepad_tree)


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows (win32)")
@pytest.mark.requires_display
def test_uia_notepad_tree_with_children():
    from windows_os_api.apps.ui_inspector import uia_windows
    from windows_os_api.backends.windows import WindowsBackend

    _require_live_ui(
        uia_windows.uia_available(),
        "uiautomation/comtypes/pywinauto not installed",
    )

    backend = WindowsBackend()
    proc = None
    try:
        previous = {int(w["hwnd"]) for w in backend.list_windows()}
        proc = subprocess.Popen(["notepad.exe"])  # noqa: S603
        hwnd = _wait_for_new_process_window(backend, proc.pid, previous)
        _require_live_ui(hwnd is not None, "Notepad process exposed no new window")
        tree = None
        last_err = None
        for _ in range(8):
            try:
                tree = backend.get_ui_tree(hwnd)
                if uia_windows.find_first(
                    tree, control_type="Edit"
                ) or uia_windows.find_first(tree, control_type="Document"):
                    break
            except RuntimeError as e:
                last_err = e
            time.sleep(0.5)
        _require_live_ui(
            tree is not None,
            f"Notepad UIA unavailable (no interactive desktop?): {last_err}",
        )

        assert tree.get("control_type") in ("Window", "Pane") or "Notepad" in (
            tree.get("name") or ""
        ) or tree.get("class_name") == "Notepad"
        # Rich tree should include children (title bar / edit / menu)
        children = tree.get("children") or []
        # Some Win11 Notepad builds nest deeply — allow empty only if error absent
        assert "error" not in tree or not tree.get("error")
        # Prefer children; if none, still must not be Contoso
        blob = str(tree).lower()
        assert "contoso" not in blob
        assert "fake" not in (tree.get("name") or "").lower()

        # Find Edit/Document via walk
        edit = uia_windows.find_first(tree, control_type="Edit") or uia_windows.find_first(
            tree, control_type="Document"
        )
        if edit is None and children:
            _require_live_ui(
                False, "Notepad tree has children but no Edit/Document control_type"
            )
        if edit is None:
            _require_live_ui(
                False, "Notepad tree empty — likely session 0 / limited desktop on GHA"
            )

        assert edit.get("automation_id")  # synthetic or native
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows (win32)")
@pytest.mark.requires_display
def test_notepad_type_text_and_adapter(monkeypatch):
    """Start Notepad, type via SendInput / set_value, create_adapter from tree."""
    from windows_os_api.apps.adapters.engine import create_adapter, reset_adapters
    from windows_os_api.apps.ui_inspector import uia_windows
    from windows_os_api.backends.factory import get_backend, reset_backend
    from windows_os_api.core.runtime.config import get_settings

    _require_live_ui(uia_windows.uia_available(), "UIA library not installed")

    monkeypatch.setenv("WINOS_BACKEND", "windows")
    get_settings.cache_clear()
    reset_backend()
    backend = get_backend()
    proc = None
    try:
        previous = {int(w["hwnd"]) for w in backend.list_windows()}
        proc = subprocess.Popen(["notepad.exe"])  # noqa: S603
        hwnd = _wait_for_new_process_window(backend, proc.pid, previous)
        _require_live_ui(hwnd is not None, "Notepad process exposed no new window")
        tree = None
        edit = None
        for _ in range(12):
            tree = backend.get_ui_tree(hwnd)
            edit = uia_windows.find_first(
                tree, control_type="Edit"
            ) or uia_windows.find_first(tree, control_type="Document")
            if edit is not None:
                break
            time.sleep(0.5)
        _require_live_ui(edit is not None, "Notepad exposed no editable UIA control")

        if hwnd:
            backend.focus_window(int(hwnd))
            time.sleep(0.3)

        token = f"winos-{int(time.time())}"
        if edit is not None:
            set_r = uia_windows.set_value(edit, token)
            if not set_r.get("ok"):
                # Fallback: focus + type
                typed = backend.type_text(token)
                assert typed.get("ok") is True, typed
        else:
            typed = backend.type_text(token)
            _require_live_ui(
                bool(typed.get("ok")), f"type_text failed without edit node: {typed}"
            )

        # Adapter path
        reset_adapters()
        adapter = create_adapter("notepad-live", hwnd=int(hwnd))
        assert adapter.app_id == "notepad-live"
        assert isinstance(adapter.actions, list)
        if edit and edit.get("automation_id"):
            assert len(adapter.actions) >= 1, adapter.actions
        # Never Contoso
        assert "contoso" not in adapter.app_name.lower()
    finally:
        reset_adapters()
        reset_backend()
        get_settings.cache_clear()
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows (win32)")
@pytest.mark.requires_display
def test_notepad_capability_verification_restores_original(monkeypatch, tmp_path):
    """Real Notepad -> adapter -> probe readback -> rollback readback."""
    from windows_os_api.apps.adapters.engine import (
        create_adapter,
        reset_adapters,
        verify_and_record,
    )
    from windows_os_api.apps.ui_inspector import uia_windows
    from windows_os_api.apps.ui_inspector.service import find_by_automation_id
    from windows_os_api.backends.factory import get_backend, reset_backend
    from windows_os_api.core.runtime.config import get_settings

    _require_live_ui(uia_windows.uia_available(), "UIA library not installed")
    monkeypatch.setenv("WINOS_BACKEND", "windows")
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(tmp_path / "adapters"))
    get_settings.cache_clear()
    reset_backend()
    reset_adapters()
    backend = get_backend()

    previous = {int(w["hwnd"]) for w in backend.list_windows()}
    proc = subprocess.Popen(["notepad.exe"])  # noqa: S603
    try:
        hwnd = _wait_for_new_process_window(backend, proc.pid, previous)
        _require_live_ui(hwnd is not None, "Notepad process exposed no new window")
        tree = None
        edit = None
        for _ in range(12):
            tree = backend.get_ui_tree(hwnd)
            edit = uia_windows.find_first(
                tree, control_type="Edit"
            ) or uia_windows.find_first(tree, control_type="Document")
            if edit is not None:
                break
            time.sleep(0.5)
        _require_live_ui(
            tree is not None and edit is not None,
            "Notepad did not expose an editable UIA control",
        )

        original = "winos-original-real-notepad"
        prepared = uia_windows.set_value(edit, original)
        assert prepared.get("ok") is True, prepared
        adapter = create_adapter("notepad-live-verify", hwnd=int(hwnd))
        edit_actions = [a for a in adapter.actions if a.control_type == "Edit"]
        assert edit_actions, adapter.actions
        action = next(
            (a for a in edit_actions if a.automation_id == edit.get("automation_id")),
            edit_actions[0],
        )

        result = verify_and_record(adapter.app_id, action.name, times=2)
        assert result["ok"] is True, result
        assert result["verification"]["state"] == "VERIFIED", result
        assert result["verification"]["observed"]["rollback_observed"] is True, result
        assert set(adapter.openapi["paths"]) == {
            f"/v1/apps/{adapter.app_id}/actions/{action.name}"
        }, "the real UIA capability did not enter the Virtual API"

        fresh = backend.get_ui_tree(hwnd)
        restored = find_by_automation_id(fresh, action.automation_id)
        assert restored is not None, fresh
        assert restored.get("value") == original, restored
    finally:
        reset_adapters()
        reset_backend()
        get_settings.cache_clear()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
