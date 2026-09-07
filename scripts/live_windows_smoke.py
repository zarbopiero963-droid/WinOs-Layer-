#!/usr/bin/env python3
"""Live smoke for WindowsBackend on win32 (GHA windows-latest / local desktop).

Exercises: system info, start_process (real PID), optional Notepad UIA tree,
type_text, screenshot. UI steps skip cleanly when no interactive desktop
(session 0 / GHA without display).
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("WINOS_BACKEND", "windows")
os.environ.setdefault("WINOS_REQUIRE_AUTH", "true")
os.environ.setdefault("WINOS_API_KEYS", '["dev-key-change-me"]')


def _have_display_session() -> bool:
    """Heuristic: interactive session likely present."""
    if sys.platform != "win32":
        return False
    # Session 0 service contexts often lack an interactive desktop
    try:
        import ctypes

        # GetSystemMetrics SM_CXSCREEN
        w = ctypes.windll.user32.GetSystemMetrics(0)
        h = ctypes.windll.user32.GetSystemMetrics(1)
        return w > 0 and h > 0
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    if sys.platform != "win32":
        print("SKIP: live_windows_smoke requires win32")
        return 0

    import psutil
    from windows_os_api.backends.windows import WindowsBackend
    from windows_os_api.core.runtime.config import get_settings
    from windows_os_api.backends.factory import reset_backend, get_backend

    sandbox = ROOT / "sandbox" / "live_windows_smoke"
    sandbox.mkdir(parents=True, exist_ok=True)
    os.environ["WINOS_SANDBOX_ROOT"] = str(sandbox)
    get_settings.cache_clear()
    reset_backend()

    backend = WindowsBackend(sandbox_root=str(sandbox))
    info = backend.get_system_info()
    print("system_info:", info)
    assert info["backend"] == "windows"
    assert info["os"] == "Windows"
    assert "contoso" not in info["hostname"].lower()

    flags = backend.capability_flags()
    print("capability_flags:", flags)
    assert flags.get("sendinput") is True

    # Real process
    started = backend.start_process("ping", ["-n", "1", "127.0.0.1"])
    print("start_process ping:", started)
    assert started.get("pid", 0) > 0

    # Displays (headless-capable)
    displays = backend.list_displays()
    print("list_displays:", displays)
    assert len(displays) >= 1

    # SendInput structures (no UI required for API success)
    kp = backend.key_press("shift")
    print("key_press:", kp)
    assert kp.get("ok") is True, kp
    assert "stub" not in str(kp).lower()

    typed_empty = backend.type_text("")
    assert typed_empty.get("ok") is True

    # Registry / FS headless must-pass
    reg = backend.registry_read(
        r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName"
    )
    print("registry:", reg)
    assert reg.get("ok") is True

    marker = sandbox / "smoke.txt"
    backend.fs_write(str(marker), "windows-live-smoke")
    assert "windows-live-smoke" in backend.fs_read(str(marker))["text"]

    ui_ok = False
    shot_ok = False
    if _have_display_session():
        # Screenshot
        shot = backend.screenshot()
        print("screenshot ok:", shot.get("ok"), "method:", shot.get("method"), "size:", shot.get("size"))
        if shot.get("ok"):
            raw = base64.b64decode(shot["data_base64"])
            assert len(raw) > 1000
            shot_ok = True
        else:
            print("NOTE: screenshot skipped/failed:", shot.get("error"))

        # Notepad UIA
        from windows_os_api.apps.ui_inspector import uia_windows

        if uia_windows.uia_available():
            proc = subprocess.Popen(["notepad.exe"])  # noqa: S603
            try:
                time.sleep(1.5)
                try:
                    tree = uia_windows.get_notepad_tree()
                    print(
                        "notepad tree:",
                        tree.get("name"),
                        tree.get("control_type"),
                        "children=",
                        len(tree.get("children") or []),
                        "source=",
                        tree.get("source"),
                    )
                    assert "contoso" not in str(tree).lower()
                    hwnd = tree.get("hwnd")
                    if hwnd:
                        backend.focus_window(int(hwnd))
                    token = f"winos-live-{int(time.time())}"
                    tr = backend.type_text(token)
                    print("type_text:", tr)
                    assert tr.get("ok") is True
                    ui_ok = True

                    # Adapter from tree
                    from windows_os_api.apps.adapters.engine import create_adapter, reset_adapters

                    reset_adapters()
                    ad = create_adapter("notepad-smoke", hwnd=int(hwnd or 0))
                    print("adapter actions:", len(ad.actions), "app_name:", ad.app_name)
                    reset_adapters()
                except RuntimeError as e:
                    print("NOTE: UIA notepad skipped:", e)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                for w in backend.list_windows():
                    if "Notepad" in (w.get("title") or ""):
                        backend.close_window(w["hwnd"])
        else:
            print("NOTE: no UIA library installed")
    else:
        print("NOTE: no interactive display session — skipped UIA/screenshot UI steps")

    # Factory
    get_settings.cache_clear()
    reset_backend()
    os.environ["WINOS_BACKEND"] = "windows"
    b = get_backend()
    assert b.name == "windows"
    print("factory backend:", b.name)

    # Optional API path
    from fastapi.testclient import TestClient
    from windows_os_api.core.runtime.app import create_app

    get_settings.cache_clear()
    reset_backend()
    app = create_app(get_settings())
    headers = {"X-API-Key": "dev-key-change-me"}
    with TestClient(app) as client:
        r = client.get("/v1/system", headers=headers)
        assert r.status_code == 200
        body = r.json()
        print("GET /v1/system:", body)
        assert body["backend"] == "windows"
        caps = client.get("/v1/capabilities", headers=headers).json()
        print("capabilities:", caps.get("backend"), caps.get("feature_flags"))
        assert caps["backend"] == "windows"

    print(
        "LIVE WINDOWS SMOKE OK —",
        f"ui_ok={ui_ok} shot_ok={shot_ok} display_session={_have_display_session()}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
