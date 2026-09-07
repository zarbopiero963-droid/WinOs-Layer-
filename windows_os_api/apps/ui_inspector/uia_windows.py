"""Windows UI Automation helpers (comtypes / uiautomation / pywinauto).

Importable on all platforms for compile/smoke tests. Runtime calls require win32.
"""
from __future__ import annotations

import sys
from typing import Any


def uia_available() -> bool:
    """Return True if a Windows UIA library is importable."""
    if sys.platform != "win32":
        return False
    for mod in ("uiautomation", "comtypes", "pywinauto"):
        try:
            __import__(mod)
            return True
        except ImportError:
            continue
    return False


def describe_backend() -> dict[str, Any]:
    """Report which UIA backends are present (safe on Linux)."""
    found: list[str] = []
    for mod in ("uiautomation", "comtypes", "pywinauto"):
        try:
            __import__(mod)
            found.append(mod)
        except ImportError:
            pass
    return {
        "platform": sys.platform,
        "uia_available": bool(found) and sys.platform == "win32",
        "modules": found,
    }


def get_notepad_tree() -> dict[str, Any]:
    """Best-effort UI tree for Notepad. Raises if UIA deps or Notepad missing."""
    if sys.platform != "win32":
        raise RuntimeError("UIA notepad tree requires Windows")
    if not uia_available():
        raise RuntimeError("No UIA library (uiautomation/comtypes/pywinauto)")

    try:
        import uiautomation as auto  # type: ignore

        win = auto.WindowControl(searchDepth=1, ClassName="Notepad")
        if not win.Exists(0, 0):
            win = auto.WindowControl(searchDepth=1, Name="Untitled - Notepad")
        if not win.Exists(1, 0.5):
            raise RuntimeError("Notepad window not found")
        return {
            "name": win.Name,
            "control_type": "Window",
            "class_name": getattr(win, "ClassName", "Notepad"),
            "children": [],
            "source": "uiautomation",
        }
    except ImportError:
        pass

    try:
        from pywinauto import Desktop  # type: ignore

        desk = Desktop(backend="uia")
        for w in desk.windows():
            title = w.window_text()
            if "Notepad" in title or w.class_name() == "Notepad":
                return {
                    "name": title,
                    "control_type": "Window",
                    "class_name": w.class_name(),
                    "children": [],
                    "source": "pywinauto",
                }
        raise RuntimeError("Notepad window not found via pywinauto")
    except ImportError as e:
        raise RuntimeError("UIA libraries present but notepad tree failed") from e
