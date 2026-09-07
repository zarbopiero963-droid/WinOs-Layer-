"""WindowsBackend — real Windows OS bindings with guarded imports.

UIA tree, SendInput mouse/keyboard, displays/screenshots are implemented for
win32. Never returns Fake Contoso CRM data.
"""
from __future__ import annotations

import base64
import platform
import sys
import time
from pathlib import Path
from typing import Any

from windows_os_api.os.terminal.allowlist import CommandRejected, resolve as resolve_command


class WindowsBackendUnavailable(RuntimeError):
    pass


def _require_windows() -> None:
    if sys.platform != "win32":
        raise WindowsBackendUnavailable("WindowsBackend requires win32 platform")


# ---------------------------------------------------------------------------
# ctypes SendInput helpers (work with or without pywin32)
# ---------------------------------------------------------------------------
def _sendinput_structs():
    """Return (ctypes, windll, INPUT, MOUSEINPUT, KEYBDINPUT, constants) or raise."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_ABSOLUTE = 0x8000
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_EXTENDEDKEY = 0x0001

    ULONG_PTR = wintypes.WPARAM

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [
            ("type", wintypes.DWORD),
            ("union", INPUT_UNION),
        ]

    return {
        "ctypes": ctypes,
        "user32": user32,
        "INPUT": INPUT,
        "MOUSEINPUT": MOUSEINPUT,
        "KEYBDINPUT": KEYBDINPUT,
        "INPUT_UNION": INPUT_UNION,
        "INPUT_MOUSE": INPUT_MOUSE,
        "INPUT_KEYBOARD": INPUT_KEYBOARD,
        "MOUSEEVENTF_MOVE": MOUSEEVENTF_MOVE,
        "MOUSEEVENTF_LEFTDOWN": MOUSEEVENTF_LEFTDOWN,
        "MOUSEEVENTF_LEFTUP": MOUSEEVENTF_LEFTUP,
        "MOUSEEVENTF_RIGHTDOWN": MOUSEEVENTF_RIGHTDOWN,
        "MOUSEEVENTF_RIGHTUP": MOUSEEVENTF_RIGHTUP,
        "MOUSEEVENTF_MIDDLEDOWN": MOUSEEVENTF_MIDDLEDOWN,
        "MOUSEEVENTF_MIDDLEUP": MOUSEEVENTF_MIDDLEUP,
        "MOUSEEVENTF_ABSOLUTE": MOUSEEVENTF_ABSOLUTE,
        "KEYEVENTF_KEYUP": KEYEVENTF_KEYUP,
        "KEYEVENTF_UNICODE": KEYEVENTF_UNICODE,
        "KEYEVENTF_EXTENDEDKEY": KEYEVENTF_EXTENDEDKEY,
    }


_VK_MAP: dict[str, int] = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "caps": 0x14,
    "capslock": 0x14,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "win": 0x5B,
    "lwin": 0x5B,
    "rwin": 0x5C,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
}


def _vk_for_key(key: str) -> int | None:
    k = key.lower().strip()
    if k in _VK_MAP:
        return _VK_MAP[k]
    if len(k) == 1:
        ch = k.upper()
        code = ord(ch)
        if ord("A") <= code <= ord("Z") or ord("0") <= code <= ord("9"):
            return code
    return None


class WindowsBackend:
    """Real Windows backend. Imports win32 / COM only when instantiated on Windows."""

    name = "windows"

    def __init__(self, sandbox_root: str = "sandbox") -> None:
        _require_windows()
        self.sandbox = Path(sandbox_root)
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self._start = time.time()
        self._psutil = None
        try:
            import psutil as _psutil

            self._psutil = _psutil
        except ImportError:
            pass
        # Guarded optional Windows deps
        self._win32api = None
        self._win32gui = None
        self._win32clipboard = None
        self._winreg = None
        try:
            import win32api  # type: ignore
            import win32gui  # type: ignore
            import win32clipboard  # type: ignore
            import winreg  # type: ignore

            self._win32api = win32api
            self._win32gui = win32gui
            self._win32clipboard = win32clipboard
            self._winreg = winreg
        except ImportError:
            pass
        self._caps = self._probe_capabilities()

    def _probe_capabilities(self) -> dict[str, bool]:
        has_uia = False
        try:
            from windows_os_api.apps.ui_inspector import uia_windows

            has_uia = uia_windows.uia_available()
        except Exception:  # noqa: BLE001
            has_uia = False
        has_mss = False
        try:
            import mss  # type: ignore  # noqa: F401

            has_mss = True
        except Exception:  # noqa: BLE001
            has_mss = False
        has_pil = False
        try:
            from PIL import ImageGrab  # type: ignore  # noqa: F401

            has_pil = True
        except Exception:  # noqa: BLE001
            has_pil = False
        return {
            "processes": self._psutil is not None,
            "filesystem": True,
            "network": self._psutil is not None,
            "system": True,
            "windows_ui": self._win32gui is not None,
            "windows_uia": has_uia,
            "clipboard": self._win32clipboard is not None,
            "screenshot": has_mss or has_pil or self._win32api is not None,
            "sendinput": True,  # ctypes user32 always present on win32
            "registry": self._winreg is not None,
            "atspi": False,
        }

    def capability_flags(self) -> dict[str, bool]:
        return dict(self._caps)

    # ------------------------------------------------------------------
    # System
    # ------------------------------------------------------------------
    def get_system_info(self) -> dict[str, Any]:
        import os

        return {
            "hostname": platform.node(),
            "os": "Windows",
            "os_version": platform.version(),
            "architecture": platform.machine(),
            "backend": self.name,
            "python": platform.python_version(),
            "user": os.environ.get("USERNAME", ""),
        }

    def get_resources(self) -> dict[str, Any]:
        if self._psutil:
            vm = self._psutil.virtual_memory()
            disk = self._psutil.disk_usage("C:\\")
            return {
                "cpu_percent": self._psutil.cpu_percent(interval=0.1),
                "memory": {
                    "total_mb": round(vm.total / 1e6, 1),
                    "used_mb": round(vm.used / 1e6, 1),
                    "percent": vm.percent,
                },
                "disk": {
                    "total_gb": round(disk.total / 1e9, 1),
                    "used_gb": round(disk.used / 1e9, 1),
                    "percent": disk.percent,
                },
            }
        return {"cpu_percent": 0, "memory": {}, "disk": {}, "note": "psutil unavailable"}

    def get_uptime(self) -> dict[str, Any]:
        if self._psutil:
            boot = self._psutil.boot_time()
            return {"uptime_seconds": time.time() - boot, "boot_time": boot}
        return {"uptime_seconds": time.time() - self._start, "boot_time": self._start}

    def power_action(self, action: str) -> dict[str, Any]:
        return {"ok": False, "error": "power actions require interactive elevation", "action": action}

    # ------------------------------------------------------------------
    # Processes
    # ------------------------------------------------------------------
    def list_processes(self) -> list[dict[str, Any]]:
        if not self._psutil:
            return []
        out = []
        for p in self._psutil.process_iter(["pid", "name", "status", "cpu_percent", "memory_info"]):
            try:
                info = p.info
                mem = info.get("memory_info")
                out.append({
                    "pid": info["pid"],
                    "name": info.get("name") or "",
                    "status": info.get("status") or "",
                    "cpu_percent": info.get("cpu_percent") or 0,
                    "memory_mb": round((mem.rss / 1e6) if mem else 0, 2),
                })
            except (self._psutil.NoSuchProcess, self._psutil.AccessDenied):
                continue
        return out

    def get_process(self, pid: int) -> dict[str, Any] | None:
        if not self._psutil:
            return None
        try:
            p = self._psutil.Process(pid)
            return {
                "pid": pid,
                "name": p.name(),
                "status": p.status(),
                "cpu_percent": p.cpu_percent(interval=0.0),
                "memory_mb": round(p.memory_info().rss / 1e6, 2),
            }
        except self._psutil.Error:
            return None

    def start_process(self, command: str, args: list[str] | None = None) -> dict[str, Any]:
        import subprocess

        cmd = [command, *(args or [])]
        proc = subprocess.Popen(cmd)  # noqa: S603
        return {"pid": proc.pid, "name": Path(command).name, "status": "running", "ok": True}

    def terminate_process(self, pid: int) -> dict[str, Any]:
        if not self._psutil:
            return {"ok": False, "error": "psutil unavailable"}
        try:
            self._psutil.Process(pid).terminate()
            return {"ok": True, "pid": pid}
        except self._psutil.Error as e:
            return {"ok": False, "error": str(e), "pid": pid}

    # ------------------------------------------------------------------
    # Apps
    # ------------------------------------------------------------------
    def discover_apps(self) -> list[dict[str, Any]]:
        apps: list[dict[str, Any]] = []
        candidates = [
            Path(r"C:\Program Files"),
            Path(r"C:\Program Files (x86)"),
            Path(os_environ_local_appdata()) / "Programs",
        ]
        for root in candidates:
            if not root.exists():
                continue
            for exe in root.rglob("*.exe"):
                if len(apps) >= 200:
                    break
                apps.append({
                    "id": exe.stem.lower().replace(" ", "-"),
                    "name": exe.stem,
                    "path": str(exe),
                    "version": "",
                    "publisher": "",
                    "source": "filesystem",
                })
        if self._winreg:
            apps.extend(self._discover_from_registry())
        return apps

    def _discover_from_registry(self) -> list[dict[str, Any]]:
        import winreg  # type: ignore

        found: list[dict[str, Any]] = []
        uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, uninstall)
        except OSError:
            return found
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(key, i)
                i += 1
                sk = winreg.OpenKey(key, sub)
                try:
                    name, _ = winreg.QueryValueEx(sk, "DisplayName")
                except OSError:
                    continue
                path = ""
                try:
                    path, _ = winreg.QueryValueEx(sk, "DisplayIcon")
                except OSError:
                    pass
                found.append({
                    "id": str(name).lower().replace(" ", "-")[:64],
                    "name": str(name),
                    "path": str(path).split(",")[0],
                    "version": "",
                    "publisher": "",
                    "source": "registry",
                })
            except OSError:
                break
        return found

    # ------------------------------------------------------------------
    # Windows
    # ------------------------------------------------------------------
    def list_windows(self) -> list[dict[str, Any]]:
        if not self._win32gui:
            return []
        result: list[dict[str, Any]] = []

        def _enum(hwnd, _):
            if self._win32gui.IsWindowVisible(hwnd):
                title = self._win32gui.GetWindowText(hwnd)
                if title:
                    result.append({"hwnd": hwnd, "title": title, "visible": True})

        self._win32gui.EnumWindows(_enum, None)
        return result

    def get_window(self, hwnd: int) -> dict[str, Any] | None:
        wins = {w["hwnd"]: w for w in self.list_windows()}
        return wins.get(hwnd)

    def focus_window(self, hwnd: int) -> dict[str, Any]:
        if not self._win32gui:
            return {"ok": False, "error": "win32gui unavailable"}
        try:
            self._win32gui.SetForegroundWindow(hwnd)
            return {"ok": True, "hwnd": hwnd}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def close_window(self, hwnd: int) -> dict[str, Any]:
        if not self._win32gui:
            return {"ok": False, "error": "win32gui unavailable"}
        import win32con  # type: ignore

        self._win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return {"ok": True, "hwnd": hwnd}

    # ------------------------------------------------------------------
    # UI Automation
    # ------------------------------------------------------------------
    def get_ui_tree(self, hwnd: int | None = None) -> dict[str, Any]:
        try:
            return self._uia_tree(hwnd)
        except Exception as e:  # noqa: BLE001
            return {
                "hwnd": hwnd,
                "name": "",
                "control_type": "Window",
                "children": [],
                "error": str(e),
                "stub": False,
                "ok": False,
            }

    def _uia_tree(self, hwnd: int | None) -> dict[str, Any]:
        """Build rich UIA tree via uiautomation → comtypes → pywinauto."""
        from windows_os_api.apps.ui_inspector import uia_windows

        return uia_windows.build_tree(hwnd)

    def find_accessible(
        self,
        name: str | None = None,
        role: str | None = None,
        *,
        exact: bool = False,
        hwnd: int | None = None,
    ) -> dict[str, Any] | None:
        tree = self.get_ui_tree(hwnd)
        stack = [tree]
        while stack:
            node = stack.pop(0)
            n = node.get("name") or ""
            r = (node.get("role") or node.get("control_type") or "").lower()
            ok_name = True
            ok_role = True
            if name is not None:
                ok_name = (n == name) if exact else (name.lower() in n.lower())
            if role is not None:
                ok_role = role.lower() in r
            if ok_name and ok_role and (name is not None or role is not None):
                return node
            stack[0:0] = list(node.get("children") or [])
        return None

    def accessible_click(self, name: str, role: str | None = None) -> dict[str, Any]:
        from windows_os_api.apps.ui_inspector import uia_windows

        node = self.find_accessible(name=name, role=role)
        if not node:
            return {"ok": False, "error": f"accessible not found: {name!r}"}
        return uia_windows.invoke_click(node)

    def accessible_set_text(self, name: str, text: str, role: str | None = None) -> dict[str, Any]:
        from windows_os_api.apps.ui_inspector import uia_windows

        node = self.find_accessible(name=name, role=role or "Edit")
        if not node:
            # Also try Document
            node = self.find_accessible(name=name, role="Document")
        if not node:
            return {"ok": False, "error": f"edit control not found: {name!r}"}
        return uia_windows.set_value(node, text)

    # ------------------------------------------------------------------
    # Input — real SendInput / mouse_event
    # ------------------------------------------------------------------
    def _screen_size(self) -> tuple[int, int]:
        if self._win32api:
            try:
                return (
                    int(self._win32api.GetSystemMetrics(0)),
                    int(self._win32api.GetSystemMetrics(1)),
                )
            except Exception:  # noqa: BLE001
                pass
        try:
            si = _sendinput_structs()
            w = int(si["user32"].GetSystemMetrics(0))
            h = int(si["user32"].GetSystemMetrics(1))
            return w, h
        except Exception:  # noqa: BLE001
            return 1920, 1080

    def mouse_move(self, x: int, y: int) -> dict[str, Any]:
        # Prefer SetCursorPos (immediate) then absolute SendInput for consistency
        if self._win32api:
            try:
                self._win32api.SetCursorPos((int(x), int(y)))
                return {"ok": True, "x": int(x), "y": int(y), "method": "SetCursorPos"}
            except Exception as e:  # noqa: BLE001
                err = str(e)
        else:
            err = "win32api unavailable"
        try:
            si = _sendinput_structs()
            sw, sh = self._screen_size()
            ax = int(int(x) * 65535 / max(sw - 1, 1))
            ay = int(int(y) * 65535 / max(sh - 1, 1))
            inp = si["INPUT"]()
            inp.type = si["INPUT_MOUSE"]
            inp.union.mi = si["MOUSEINPUT"](
                ax,
                ay,
                0,
                si["MOUSEEVENTF_MOVE"] | si["MOUSEEVENTF_ABSOLUTE"],
                0,
                0,
            )
            sent = si["user32"].SendInput(1, si["ctypes"].byref(inp), si["ctypes"].sizeof(si["INPUT"]))
            if sent != 1:
                return {"ok": False, "error": f"SendInput returned {sent}", "x": x, "y": y}
            return {"ok": True, "x": int(x), "y": int(y), "method": "SendInput"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{err}; {e}", "x": x, "y": y}

    def mouse_click(self, x: int, y: int, button: str = "left") -> dict[str, Any]:
        moved = self.mouse_move(x, y)
        if not moved.get("ok"):
            return {**moved, "button": button, "ok": False}
        button = (button or "left").lower()
        # Prefer win32api.mouse_event when available
        if self._win32api:
            try:
                import win32con  # type: ignore

                down_up = {
                    "left": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
                    "right": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP),
                    "middle": (win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP),
                }.get(button, (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP))
                self._win32api.mouse_event(down_up[0], 0, 0, 0, 0)
                self._win32api.mouse_event(down_up[1], 0, 0, 0, 0)
                return {"ok": True, "x": int(x), "y": int(y), "button": button, "method": "mouse_event"}
            except Exception as e:  # noqa: BLE001
                mouse_err = str(e)
        else:
            mouse_err = "win32api unavailable"
        try:
            si = _sendinput_structs()
            flags = {
                "left": (si["MOUSEEVENTF_LEFTDOWN"], si["MOUSEEVENTF_LEFTUP"]),
                "right": (si["MOUSEEVENTF_RIGHTDOWN"], si["MOUSEEVENTF_RIGHTUP"]),
                "middle": (si["MOUSEEVENTF_MIDDLEDOWN"], si["MOUSEEVENTF_MIDDLEUP"]),
            }.get(button, (si["MOUSEEVENTF_LEFTDOWN"], si["MOUSEEVENTF_LEFTUP"]))
            inputs = (si["INPUT"] * 2)()
            for i, fl in enumerate(flags):
                inputs[i].type = si["INPUT_MOUSE"]
                inputs[i].union.mi = si["MOUSEINPUT"](0, 0, 0, fl, 0, 0)
            sent = si["user32"].SendInput(2, si["ctypes"].byref(inputs), si["ctypes"].sizeof(si["INPUT"]))
            if sent != 2:
                return {
                    "ok": False,
                    "error": f"SendInput click returned {sent} ({mouse_err})",
                    "x": x,
                    "y": y,
                    "button": button,
                }
            return {"ok": True, "x": int(x), "y": int(y), "button": button, "method": "SendInput"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{mouse_err}; {e}", "x": x, "y": y, "button": button}

    def mouse_drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        button: str = "left",
        *,
        steps: int = 10,
    ) -> dict[str, Any]:
        """Drag from (x1,y1) to (x2,y2) using SendInput / mouse_event."""
        button = (button or "left").lower()
        moved = self.mouse_move(x1, y1)
        if not moved.get("ok"):
            return {**moved, "ok": False}
        try:
            si = _sendinput_structs()
            down = {
                "left": si["MOUSEEVENTF_LEFTDOWN"],
                "right": si["MOUSEEVENTF_RIGHTDOWN"],
                "middle": si["MOUSEEVENTF_MIDDLEDOWN"],
            }.get(button, si["MOUSEEVENTF_LEFTDOWN"])
            up = {
                "left": si["MOUSEEVENTF_LEFTUP"],
                "right": si["MOUSEEVENTF_RIGHTUP"],
                "middle": si["MOUSEEVENTF_MIDDLEUP"],
            }.get(button, si["MOUSEEVENTF_LEFTUP"])
            inp = si["INPUT"]()
            inp.type = si["INPUT_MOUSE"]
            inp.union.mi = si["MOUSEINPUT"](0, 0, 0, down, 0, 0)
            if si["user32"].SendInput(1, si["ctypes"].byref(inp), si["ctypes"].sizeof(si["INPUT"])) != 1:
                return {"ok": False, "error": "SendInput mouse down failed"}
            for i in range(1, max(steps, 1) + 1):
                t = i / max(steps, 1)
                xi = int(x1 + (x2 - x1) * t)
                yi = int(y1 + (y2 - y1) * t)
                self.mouse_move(xi, yi)
            inp2 = si["INPUT"]()
            inp2.type = si["INPUT_MOUSE"]
            inp2.union.mi = si["MOUSEINPUT"](0, 0, 0, up, 0, 0)
            if si["user32"].SendInput(1, si["ctypes"].byref(inp2), si["ctypes"].sizeof(si["INPUT"])) != 1:
                return {"ok": False, "error": "SendInput mouse up failed"}
            return {"ok": True, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "button": button}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def _send_vk(self, vk: int, *, key_up: bool = False, extended: bool = False) -> bool:
        si = _sendinput_structs()
        flags = si["KEYEVENTF_KEYUP"] if key_up else 0
        if extended:
            flags |= si["KEYEVENTF_EXTENDEDKEY"]
        inp = si["INPUT"]()
        inp.type = si["INPUT_KEYBOARD"]
        inp.union.ki = si["KEYBDINPUT"](vk, 0, flags, 0, 0)
        return si["user32"].SendInput(1, si["ctypes"].byref(inp), si["ctypes"].sizeof(si["INPUT"])) == 1

    def _send_unicode_char(self, ch: str) -> bool:
        si = _sendinput_structs()
        code = ord(ch)
        inputs = (si["INPUT"] * 2)()
        for i, up in enumerate((False, True)):
            flags = si["KEYEVENTF_UNICODE"]
            if up:
                flags |= si["KEYEVENTF_KEYUP"]
            inputs[i].type = si["INPUT_KEYBOARD"]
            inputs[i].union.ki = si["KEYBDINPUT"](0, code, flags, 0, 0)
        return si["user32"].SendInput(2, si["ctypes"].byref(inputs), si["ctypes"].sizeof(si["INPUT"])) == 2

    def key_press(self, key: str, modifiers: list[str] | None = None) -> dict[str, Any]:
        mods = [m.lower() for m in (modifiers or [])]
        mod_vks = []
        for m in mods:
            vk = _vk_for_key(m)
            if vk is None:
                return {"ok": False, "error": f"unknown modifier: {m}", "key": key}
            mod_vks.append(vk)
        key_vk = _vk_for_key(key)
        try:
            for vk in mod_vks:
                if not self._send_vk(vk, key_up=False):
                    return {"ok": False, "error": "SendInput modifier down failed", "key": key}
            if key_vk is not None:
                if not self._send_vk(key_vk, key_up=False):
                    return {"ok": False, "error": "SendInput key down failed", "key": key}
                if not self._send_vk(key_vk, key_up=True):
                    return {"ok": False, "error": "SendInput key up failed", "key": key}
            elif len(key) == 1:
                if not self._send_unicode_char(key):
                    return {"ok": False, "error": "SendInput unicode failed", "key": key}
            else:
                return {"ok": False, "error": f"unknown key: {key}", "key": key}
            for vk in reversed(mod_vks):
                if not self._send_vk(vk, key_up=True):
                    return {"ok": False, "error": "SendInput modifier up failed", "key": key}
            return {"ok": True, "key": key, "modifiers": mods, "method": "SendInput"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "key": key, "modifiers": mods}

    def type_text(self, text: str) -> dict[str, Any]:
        if text is None:
            return {"ok": False, "error": "text is None"}
        try:
            for ch in text:
                if ch == "\n":
                    if not self._send_vk(_VK_MAP["enter"], key_up=False):
                        return {"ok": False, "error": "SendInput enter down failed", "length": 0}
                    if not self._send_vk(_VK_MAP["enter"], key_up=True):
                        return {"ok": False, "error": "SendInput enter up failed", "length": 0}
                elif ch == "\t":
                    if not self._send_vk(_VK_MAP["tab"], key_up=False):
                        return {"ok": False, "error": "SendInput tab failed", "length": 0}
                    if not self._send_vk(_VK_MAP["tab"], key_up=True):
                        return {"ok": False, "error": "SendInput tab up failed", "length": 0}
                else:
                    if not self._send_unicode_char(ch):
                        return {"ok": False, "error": f"SendInput unicode failed for {ch!r}", "length": 0}
            return {"ok": True, "length": len(text), "method": "SendInput"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "length": 0}

    # ------------------------------------------------------------------
    # Clipboard
    # ------------------------------------------------------------------
    def clipboard_get(self) -> dict[str, Any]:
        if not self._win32clipboard:
            return {"text": "", "format": "text", "error": "clipboard unavailable"}
        import win32con  # type: ignore

        self._win32clipboard.OpenClipboard()
        try:
            data = self._win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return {"text": data, "format": "text"}
        except Exception:  # noqa: BLE001
            return {"text": "", "format": "text"}
        finally:
            self._win32clipboard.CloseClipboard()

    def clipboard_set(self, text: str) -> dict[str, Any]:
        if not self._win32clipboard:
            return {"ok": False, "error": "clipboard unavailable"}
        import win32con  # type: ignore

        self._win32clipboard.OpenClipboard()
        try:
            self._win32clipboard.EmptyClipboard()
            self._win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            return {"ok": True, "length": len(text)}
        finally:
            self._win32clipboard.CloseClipboard()

    # ------------------------------------------------------------------
    # Display / screenshot
    # ------------------------------------------------------------------
    def list_displays(self) -> list[dict[str, Any]]:
        # Prefer EnumDisplayMonitors via win32api
        if self._win32api:
            try:
                import win32api  # type: ignore

                monitors = win32api.EnumDisplayMonitors(None, None)
                out: list[dict[str, Any]] = []
                for i, mon in enumerate(monitors):
                    # mon = (hMonitor, hdcMonitor, (left, top, right, bottom))
                    rect = mon[2]
                    left, top, right, bottom = rect
                    w = int(right - left)
                    h = int(bottom - top)
                    out.append({
                        "id": i,
                        "name": f"Display {i}",
                        "width": w,
                        "height": h,
                        "left": int(left),
                        "top": int(top),
                        "primary": i == 0,
                        "scale": 1.0,
                        "source": "EnumDisplayMonitors",
                    })
                if out:
                    # Mark primary via GetSystemMetrics SM_CXSCREEN match when possible
                    try:
                        pw = int(self._win32api.GetSystemMetrics(0))
                        ph = int(self._win32api.GetSystemMetrics(1))
                        for d in out:
                            if d["width"] == pw and d["height"] == ph and d["left"] == 0 and d["top"] == 0:
                                d["primary"] = True
                            elif d["id"] != 0:
                                d["primary"] = False
                    except Exception:  # noqa: BLE001
                        pass
                    return out
            except Exception:  # noqa: BLE001
                pass
            try:
                w = int(self._win32api.GetSystemMetrics(0))
                h = int(self._win32api.GetSystemMetrics(1))
                if w > 0 and h > 0:
                    return [{
                        "id": 0,
                        "name": "Primary",
                        "width": w,
                        "height": h,
                        "primary": True,
                        "scale": 1.0,
                        "source": "GetSystemMetrics",
                    }]
            except Exception:  # noqa: BLE001
                pass
        try:
            import mss  # type: ignore

            with mss.mss() as sct:
                out = []
                for i, mon in enumerate(sct.monitors[1:], start=0):
                    out.append({
                        "id": i,
                        "name": f"Display {i}",
                        "width": mon["width"],
                        "height": mon["height"],
                        "left": mon.get("left", 0),
                        "top": mon.get("top", 0),
                        "primary": i == 0,
                        "scale": 1.0,
                        "source": "mss",
                    })
                if out:
                    return out
        except Exception:  # noqa: BLE001
            pass
        # Last resort: GetSystemMetrics via ctypes
        try:
            si = _sendinput_structs()
            w = int(si["user32"].GetSystemMetrics(0))
            h = int(si["user32"].GetSystemMetrics(1))
            if w > 0 and h > 0:
                return [{
                    "id": 0,
                    "name": "Primary",
                    "width": w,
                    "height": h,
                    "primary": True,
                    "scale": 1.0,
                    "source": "ctypes.GetSystemMetrics",
                }]
        except Exception:  # noqa: BLE001
            pass
        return [{
            "id": 0,
            "name": "Primary",
            "width": 0,
            "height": 0,
            "primary": True,
            "scale": 1.0,
            "note": "display metrics unavailable",
        }]

    def screenshot(self, display_id: int | None = None) -> dict[str, Any]:
        idx = display_id if display_id is not None else 0
        # 1) mss
        try:
            import mss  # type: ignore
            from mss.tools import to_png  # type: ignore

            with mss.mss() as sct:
                monitors = sct.monitors[1:]
                if not monitors:
                    mon = sct.monitors[0]
                elif idx < 0 or idx >= len(monitors):
                    mon = monitors[0]
                    idx = 0
                else:
                    mon = monitors[idx]
                shot = sct.grab(mon)
                png = to_png(shot.rgb, shot.size)
                if not png or len(png) < 8:
                    raise RuntimeError("mss returned empty PNG")
                return {
                    "ok": True,
                    "display_id": idx,
                    "format": "png",
                    "width": shot.width,
                    "height": shot.height,
                    "size": len(png),
                    "data_base64": base64.b64encode(png).decode("ascii"),
                    "method": "mss",
                }
        except Exception as mss_err:  # noqa: BLE001
            mss_detail = str(mss_err)

        # 2) Pillow ImageGrab
        try:
            from io import BytesIO

            from PIL import ImageGrab  # type: ignore

            img = ImageGrab.grab(all_screens=False)
            buf = BytesIO()
            img.save(buf, format="PNG")
            png = buf.getvalue()
            if not png or len(png) < 8:
                raise RuntimeError("Pillow ImageGrab returned empty PNG")
            return {
                "ok": True,
                "display_id": idx,
                "format": "png",
                "width": img.width,
                "height": img.height,
                "size": len(png),
                "data_base64": base64.b64encode(png).decode("ascii"),
                "method": "Pillow.ImageGrab",
            }
        except Exception as pil_err:  # noqa: BLE001
            pil_detail = str(pil_err)

        # 3) win32ui BitBlt
        try:
            import win32gui  # type: ignore
            import win32ui  # type: ignore
            import win32con  # type: ignore
            from io import BytesIO

            w = self._win32api.GetSystemMetrics(0) if self._win32api else 0
            h = self._win32api.GetSystemMetrics(1) if self._win32api else 0
            if w <= 0 or h <= 0:
                raise RuntimeError("invalid screen size for BitBlt")
            hwnd = win32gui.GetDesktopWindow()
            hdc = win32gui.GetWindowDC(hwnd)
            src = win32ui.CreateDCFromHandle(hdc)
            mem = src.CreateCompatibleDC()
            bmp = win32ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(src, w, h)
            mem.SelectObject(bmp)
            mem.BitBlt((0, 0), (w, h), src, (0, 0), win32con.SRCCOPY)
            # Convert via Pillow if available
            try:
                from PIL import Image  # type: ignore

                bmpinfo = bmp.GetInfo()
                bmpstr = bmp.GetBitmapBits(True)
                img = Image.frombuffer(
                    "RGB",
                    (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                    bmpstr,
                    "raw",
                    "BGRX",
                    0,
                    1,
                )
                buf = BytesIO()
                img.save(buf, format="PNG")
                png = buf.getvalue()
            finally:
                win32gui.DeleteObject(bmp.GetHandle())
                mem.DeleteDC()
                src.DeleteDC()
                win32gui.ReleaseDC(hwnd, hdc)
            if not png or len(png) < 8:
                raise RuntimeError("BitBlt PNG empty")
            return {
                "ok": True,
                "display_id": idx,
                "format": "png",
                "width": w,
                "height": h,
                "size": len(png),
                "data_base64": base64.b64encode(png).decode("ascii"),
                "method": "win32ui.BitBlt",
            }
        except Exception as gdi_err:  # noqa: BLE001
            gdi_detail = str(gdi_err)

        return {
            "ok": False,
            "error": "screenshot failed (tried mss, Pillow, BitBlt)",
            "detail": {
                "mss": locals().get("mss_detail"),
                "pillow": locals().get("pil_detail"),
                "bitblt": locals().get("gdi_detail"),
            },
            "display_id": display_id,
        }

    # ------------------------------------------------------------------
    # Filesystem
    # ------------------------------------------------------------------
    def fs_list(self, path: str) -> list[dict[str, Any]]:
        p = Path(path)
        if not p.exists():
            return []
        return [
            {"name": c.name, "path": str(c), "is_dir": c.is_dir(), "size": c.stat().st_size if c.is_file() else 0}
            for c in p.iterdir()
        ]

    def fs_read(self, path: str, max_bytes: int = 65536) -> dict[str, Any]:
        data = Path(path).read_bytes()[:max_bytes]
        return {"path": path, "size": len(data), "text": data.decode("utf-8", errors="replace")}

    def fs_write(self, path: str, content: str) -> dict[str, Any]:
        Path(path).write_text(content, encoding="utf-8")
        return {"ok": True, "path": path, "bytes": len(content.encode())}

    def fs_delete(self, path: str) -> dict[str, Any]:
        Path(path).unlink(missing_ok=True)
        return {"ok": True, "path": path}

    def list_drives(self) -> list[dict[str, Any]]:
        if self._psutil:
            return [{"letter": p.device, "fs": p.fstype, "total_gb": 0, "free_gb": 0} for p in self._psutil.disk_partitions()]
        return []

    def network_interfaces(self) -> list[dict[str, Any]]:
        if not self._psutil:
            return []
        out = []
        for name, addrs in self._psutil.net_if_addrs().items():
            out.append({"name": name, "addresses": [a.address for a in addrs], "up": True})
        return out

    def network_connections(self) -> list[dict[str, Any]]:
        if not self._psutil:
            return []
        out = []
        for c in self._psutil.net_connections(kind="inet")[:100]:
            out.append({
                "local": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "",
                "remote": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "",
                "status": c.status,
                "pid": c.pid,
            })
        return out

    def list_services(self) -> list[dict[str, Any]]:
        return [{"name": "WinOsApi", "status": "unknown", "note": "use pywin32 service APIs"}]

    def control_service(self, name: str, action: str) -> dict[str, Any]:
        return {"ok": False, "error": "service control requires elevated pywin32", "name": name, "action": action}

    def audio_devices(self) -> list[dict[str, Any]]:
        return []

    def audio_volume(self) -> dict[str, Any]:
        return {"volume": None, "muted": None}

    def list_devices(self) -> list[dict[str, Any]]:
        return []

    def list_printers(self) -> list[dict[str, Any]]:
        return []

    def list_users(self) -> list[dict[str, Any]]:
        import os

        return [{"username": os.environ.get("USERNAME", ""), "domain": os.environ.get("USERDOMAIN", ""), "admin": False}]

    def list_sessions(self) -> list[dict[str, Any]]:
        return [{"id": 1, "user": self.list_users()[0]["username"], "state": "Active"}]

    def registry_read(self, path: str, name: str | None = None) -> dict[str, Any]:
        r"""Read a registry value. path like HKLM\SOFTWARE\... or HKCU\Environment."""
        if not self._winreg:
            return {"ok": False, "error": "winreg unavailable", "path": path, "name": name}
        import winreg  # type: ignore

        hive_map = {
            "HKLM": winreg.HKEY_LOCAL_MACHINE,
            "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
            "HKCU": winreg.HKEY_CURRENT_USER,
            "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
            "HKCR": winreg.HKEY_CLASSES_ROOT,
            "HKU": winreg.HKEY_USERS,
        }
        raw = path.replace("/", "\\")
        parts = raw.split("\\", 1)
        if len(parts) != 2:
            return {"ok": False, "error": "path must be HIVE\\subkey", "path": path}
        hive = hive_map.get(parts[0]) or hive_map.get(parts[0].upper())
        if hive is None:
            return {"ok": False, "error": f"unknown hive: {parts[0]}", "path": path}
        subkey = parts[1]
        try:
            key = winreg.OpenKey(hive, subkey)
        except OSError as e:
            return {"ok": False, "error": str(e), "path": path, "name": name}
        try:
            if name is None:
                values = {}
                i = 0
                while True:
                    try:
                        vn, vv, _vt = winreg.EnumValue(key, i)
                        values[vn] = vv
                        i += 1
                    except OSError:
                        break
                return {"ok": True, "path": path, "values": values}
            value, vtype = winreg.QueryValueEx(key, name)
            return {"ok": True, "path": path, "name": name, "value": value, "type": int(vtype)}
        except OSError as e:
            return {"ok": False, "error": str(e), "path": path, "name": name}
        finally:
            winreg.CloseKey(key)

    def registry_write(self, path: str, name: str, value: Any) -> dict[str, Any]:
        return {"ok": False, "error": "registry write requires elevation", "path": path}

    def terminal_execute(self, command: str, policy: str = "ALLOW") -> dict[str, Any]:
        policy = policy.upper()
        if policy == "DENY":
            return {"ok": False, "policy": "DENY", "error": "denied"}
        if policy not in ("ALLOW", "ADMIN"):
            return {"ok": False, "error": f"unknown policy: {policy}"}
        # Enforced HERE, at the point that actually executes — not only in the
        # os/terminal/service.py wrapper. Same lesson as the adapter sandbox:
        # a check a caller can skip is not a check. There is no shell to inject
        # into any more; an unregistered command simply does not run.
        try:
            argv = resolve_command(command, policy)
        except CommandRejected as exc:
            return {"ok": False, "policy": "DENY", "error": str(exc), "command": command}
        import subprocess

        try:
            r = subprocess.run(  # noqa: S603
                argv, shell=False, capture_output=True, text=True, timeout=30
            )
            return {
                "ok": r.returncode == 0,
                "policy": policy,
                "stdout": r.stdout[:8000],
                "stderr": r.stderr[:2000],
                "exit_code": r.returncode,
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "policy": policy}


def os_environ_local_appdata() -> str:
    import os

    return os.environ.get("LOCALAPPDATA", r"C:\Users\Public")
