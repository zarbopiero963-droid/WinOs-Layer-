#!/usr/bin/env python3
"""Live smoke: Linux UI control — mousepad, windows, AT-SPI tree, type, screenshot."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("WINOS_BACKEND", "linux")
os.environ.setdefault("DISPLAY", os.environ.get("DISPLAY") or ":2")


def main() -> int:
    from windows_os_api.backends.linux import LinuxBackend

    mousepad = sys.argv[1] if len(sys.argv) > 1 else "/usr/bin/mousepad"
    if not Path(mousepad).is_file():
        print(json.dumps({"ok": False, "error": f"mousepad not found: {mousepad}"}))
        return 1

    sandbox = ROOT / "sandbox" / "ui_smoke"
    sandbox.mkdir(parents=True, exist_ok=True)
    backend = LinuxBackend(sandbox_root=str(sandbox))

    env = dict(os.environ)
    proc = subprocess.Popen(  # noqa: S603
        [mousepad],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    summary: dict = {
        "ok": False,
        "display": env.get("DISPLAY"),
        "mousepad": mousepad,
        "pid": proc.pid,
        "method": None,
        "screenshot": "/tmp/winos_ui_smoke.png",
    }
    try:
        time.sleep(1.5)
        windows = backend.list_windows()
        summary["windows_count"] = len(windows)
        summary["windows"] = [
            {"hwnd": w.get("hwnd"), "title": w.get("title")} for w in windows[:12]
        ]
        mp = next((w for w in windows if "Mousepad" in (w.get("title") or "")), None)
        if mp is None:
            summary["error"] = "mousepad window not listed"
            print(json.dumps(summary, indent=2))
            return 1

        backend.focus_window(mp["hwnd"])
        tree = backend.get_ui_tree()
        summary["ui_tree"] = {
            "supported": tree.get("supported", True),
            "backend": tree.get("backend"),
            "children": len(tree.get("children") or []),
            "error": tree.get("error"),
        }

        token = "WinOS-OK"
        typed_ok = False
        if tree.get("supported") is not False and not tree.get("error"):
            r = backend.accessible_set_text("Mousepad", token)
            if r.get("ok"):
                typed_ok = True
                summary["method"] = r.get("method") or "atspi"
            else:
                node = backend.find_accessible(role="text")
                if node is not None:
                    r2 = backend.accessible_set_text(node.get("name") or "", token, role="text")
                    if r2.get("ok"):
                        typed_ok = True
                        summary["method"] = r2.get("method") or "atspi"

        if not typed_ok:
            # xdotool fallback into focused window
            typed = backend.type_text(token)
            typed_ok = bool(typed.get("ok"))
            summary["method"] = "xdotool"
            summary["type_result"] = typed

        shot = backend.screenshot()
        summary["screenshot_ok"] = bool(shot.get("ok"))
        summary["screenshot_size"] = {
            "width": shot.get("width"),
            "height": shot.get("height"),
        }
        if shot.get("ok") and shot.get("data_base64"):
            import base64

            png = base64.b64decode(shot["data_base64"])
            out = Path("/tmp/winos_ui_smoke.png")
            out.write_bytes(png)
            summary["screenshot_bytes"] = len(png)

        summary["ok"] = bool(typed_ok and shot.get("ok") and mp is not None)
        print(json.dumps(summary, indent=2))
        return 0 if summary["ok"] else 1
    finally:
        try:
            for w in backend.list_windows():
                if "Mousepad" in (w.get("title") or ""):
                    backend.close_window(w["hwnd"])
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
