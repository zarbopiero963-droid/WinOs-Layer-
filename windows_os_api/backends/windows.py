"""WindowsBackend — real Windows OS bindings with guarded imports.

UIA tree, SendInput mouse/keyboard, displays/screenshots are implemented for
win32. Never returns Fake Contoso CRM data.
"""
from __future__ import annotations

import base64
import ipaddress
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from windows_os_api.os.capability import DiscoveryFailed
from windows_os_api.os.network import dns as _dns
from windows_os_api.os.network.validation import (
    NetworkRejected,
    validate_host,
    validate_ping,
)

from windows_os_api.os.terminal.allowlist import CommandRejected, resolve as resolve_command
from windows_os_api.os.windows.geometry import (
    GeometryRejected,
    validate_position,
    validate_size,
)
from windows_os_api.os.windows.errors import (
    FOCUS_NOT_GRANTED,
    TOOL_UNAVAILABLE,
    WINDOW_NOT_FOUND,
    WINDOW_STILL_OPEN,
    failure,
)
from windows_os_api.os.input.validation import (
    InputRejected,
    validate_button,
    validate_hotkey,
    validate_key,
    validate_scroll,
    validate_steps,
)


class WindowsBackendUnavailable(RuntimeError):
    pass


# Capability che questo backend NON implementa affatto — distinte da quelle che
# implementa ma che mancano su una macchina specifica. L'audio richiederebbe
# Core Audio COM via pycaw, che l'owner ha deciso di non aggiungere adesso
# (decisione D4-B, issue #6): nessuna installazione sulla macchina lo abilita,
# e dirlo e' diverso dal dire "qui non c'e'".
_WINDOWS_NOT_IMPLEMENTED = frozenset({"audio"})


def _require_windows() -> None:
    if sys.platform != "win32":
        raise WindowsBackendUnavailable("WindowsBackend requires win32 platform")


def _looks_like_ipv4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return True


# Windows prints "Received = 2" where Linux prints "2 received", and the word is
# localised on a non-English machine — hence the digits-after-'=' fallback.
_PING_RECEIVED_WIN = re.compile(r"Received\s*=\s*(\d+)", re.IGNORECASE)


def _parse_ping_received_windows(output: str) -> int | None:
    """Replies received, or None when the summary cannot be read.

    None means "not parsed" and is reported as such rather than as 0, which
    would be an invented answer — and the wrong one, since exit code 0 already
    says at least one reply arrived.
    """
    match = _PING_RECEIVED_WIN.search(output or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


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
        # Returned explicitly rather than reached through `ctypes.wintypes`:
        # that attribute only exists because the import above happened to bind
        # it, which is not something a caller should have to know.
        "wintypes": wintypes,
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
        # Wheel. On Windows a scroll is a mouse event carrying a signed delta,
        # not a button press as it is on X11 — WHEEL_DELTA (120) is one notch.
        "MOUSEEVENTF_WHEEL": 0x0800,
        "MOUSEEVENTF_HWHEEL": 0x01000,
        "WHEEL_DELTA": 120,
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
    NOT_IMPLEMENTED = _WINDOWS_NOT_IMPLEMENTED

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
        # Services and printers come from separate pywin32 modules. Imported
        # apart from the block above so that one missing module does not take
        # the others down with it — they are independent capabilities and the
        # capability probe reports them independently.
        self._win32service = None
        self._win32print = None
        try:
            import win32service  # type: ignore

            self._win32service = win32service
        except ImportError:
            pass
        try:
            import win32print  # type: ignore

            self._win32print = win32print
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
            "services": self._win32service is not None,
            "printers": self._win32print is not None,
            "devices": self._win32api is not None,
            # D4-B: l'audio su Windows richiederebbe Core Audio COM (pycaw), che
            # l'owner ha deciso di non aggiungere adesso. Dichiarato non
            # supportato, non finto-vuoto: una lista vuota direbbe "questa
            # macchina non ha dispositivi audio", che e' falso su ogni PC.
            "audio": False,
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

    def active_window(self) -> int | None:
        """Which window holds the foreground, or None if that cannot be read."""
        if not self._win32gui:
            return None
        try:
            hwnd = int(self._win32gui.GetForegroundWindow())
        except Exception:  # noqa: BLE001
            return None
        return hwnd or None

    def focus_window(self, hwnd: int) -> dict[str, Any]:
        """Give a window the foreground, and CHECK that it got it.

        `SetForegroundWindow` is one of the calls Windows is entitled to refuse:
        a process that does not own the foreground cannot simply take it, and
        the documented behaviour is to flash the taskbar button instead. It
        raises on some failures and returns quietly on others, so neither
        "it did not raise" nor its return value is enough — `GetForegroundWindow`
        is.
        """
        if not self._win32gui:
            return failure(TOOL_UNAVAILABLE, "win32gui unavailable", hwnd=hwnd)
        if self.window_geometry(hwnd) is None:
            return failure(WINDOW_NOT_FOUND, f"window {hwnd} not found", hwnd=hwnd)

        detail = ""
        try:
            self._win32gui.SetForegroundWindow(hwnd)
        except Exception as e:  # noqa: BLE001
            # Not returned yet: Windows may have granted the foreground anyway,
            # and the readback below is what decides.
            detail = str(e)

        active = self.active_window()
        if active is None:
            return failure(
                FOCUS_NOT_GRANTED,
                "could not read which window holds the foreground"
                + (f": {detail}" if detail else ""),
                hwnd=hwnd, verified=False,
            )
        if active != hwnd:
            return failure(
                FOCUS_NOT_GRANTED,
                f"window {active} holds the foreground, not {hwnd}"
                + (f" ({detail})" if detail else ""),
                hwnd=hwnd, active_window=active, verified=True,
            )
        return {"ok": True, "hwnd": hwnd, "active_window": active, "verified": True}

    def close_window(self, hwnd: int, timeout: float = 5.0) -> dict[str, Any]:
        """Ask a window to close, and WAIT to see whether it did.

        `PostMessage` is **asynchronous**: it returns as soon as the message is
        queued, which is why the previous version's `{"ok": True}` meant no more
        than "the message was posted". An application with an unsaved document
        puts up "save changes?" and stays open, and the old answer said it had
        closed.

        So the message is posted and then `IsWindow` is polled until the handle
        stops naming a window. WINDOW_STILL_OPEN is the honest answer when it
        does not — the request was delivered and refused, which is a different
        outcome from both success and failure.

        Note it closes a WINDOW, not an application: a program with other
        windows open goes on running.
        """
        if not self._win32gui:
            return failure(TOOL_UNAVAILABLE, "win32gui unavailable", hwnd=hwnd)
        if self.window_geometry(hwnd) is None:
            return failure(WINDOW_NOT_FOUND, f"window {hwnd} not found", hwnd=hwnd)

        try:
            import win32con  # type: ignore

            self._win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception as e:  # noqa: BLE001
            return failure(TOOL_UNAVAILABLE, f"could not post WM_CLOSE: {e}", hwnd=hwnd)

        deadline = time.time() + max(timeout, 0.1)
        while time.time() < deadline:
            if self.window_geometry(hwnd) is None:
                return {"ok": True, "hwnd": hwnd, "closed": True, "verified": True}
            time.sleep(0.05)

        return failure(
            WINDOW_STILL_OPEN,
            f"window {hwnd} was asked to close and is still open after "
            f"{timeout:g}s — an unsaved document or a confirmation dialog will "
            "do this",
            hwnd=hwnd, closed=False, verified=True,
        )

    # ------------------------------------------------------------------
    # Window geometry / state
    #
    # Same contract as LinuxBackend: the result is read back from the OS with
    # GetWindowRect / IsIconic / IsZoomed, not inferred from the call returning
    # without raising. A window manager — or Windows itself, via the window's
    # min/max tracking size — is free to clamp what it was asked for, so the
    # request and the result are reported as two different things.
    # ------------------------------------------------------------------
    def window_geometry(self, hwnd: int) -> dict[str, int] | None:
        """Geometry from GetWindowRect, or None when the handle is not a window."""
        if not self._win32gui:
            return None
        try:
            if not self._win32gui.IsWindow(hwnd):
                return None
            left, top, right, bottom = self._win32gui.GetWindowRect(hwnd)
        except Exception:  # noqa: BLE001
            return None
        return {"x": left, "y": top, "width": right - left, "height": bottom - top}

    def _window_state(self, hwnd: int) -> tuple[str | None, str | None]:
        """`(state, why it could not be determined)` — exactly one is not None.

        The reason travels with the answer because the first CI run of this code
        reported `state: None` for maximize and restore with nothing to say why,
        and a result that cannot explain itself costs a whole round to diagnose.

        `IsIconic` is kept — it demonstrably works on the runner, since minimize
        passed there. `IsZoomed` is not: it is the one call the failing three had
        in common, and pywin32 does not reliably expose it. `GetWindowPlacement`
        answers the same question from a binding that is always present.
        """
        if not self._win32gui:
            return None, "win32gui unavailable"
        try:
            if not self._win32gui.IsWindow(hwnd):
                return None, f"window {hwnd} not found"
            if self._win32gui.IsIconic(hwnd):
                return "minimized", None
        except Exception as e:  # noqa: BLE001
            return None, f"IsWindow/IsIconic failed: {e}"
        try:
            import win32con  # type: ignore

            show_cmd = self._win32gui.GetWindowPlacement(hwnd)[1]
        except Exception as e:  # noqa: BLE001
            return None, f"could not read window placement: {e}"
        if show_cmd == win32con.SW_SHOWMAXIMIZED:
            return "maximized", None
        if show_cmd in (win32con.SW_SHOWMINIMIZED, win32con.SW_MINIMIZE,
                        win32con.SW_SHOWMINNOACTIVE):
            return "minimized", None
        return "normal", None

    def _move_or_resize(
        self, hwnd: int, x: int | None, y: int | None, w: int | None, h: int | None,
        requested: dict[str, int],
    ) -> dict[str, Any]:
        if not self._win32gui:
            return {"ok": False, "error": "win32gui unavailable", "hwnd": hwnd}
        before = self.window_geometry(hwnd)
        if before is None:
            return {"ok": False, "error": f"window {hwnd} not found", "hwnd": hwnd}
        try:
            self._win32gui.MoveWindow(
                hwnd,
                before["x"] if x is None else x,
                before["y"] if y is None else y,
                before["width"] if w is None else w,
                before["height"] if h is None else h,
                True,
            )
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "hwnd": hwnd, "geometry": before}
        after = self.window_geometry(hwnd)
        if after is None:
            return {"ok": False, "error": "window disappeared during the operation",
                    "hwnd": hwnd}
        return {"ok": True, "hwnd": hwnd, "requested": requested,
                "geometry": after, "previous": before}

    def move_window(self, hwnd: int, x: int, y: int) -> dict[str, Any]:
        try:
            x, y = validate_position(x, y)
        except GeometryRejected as exc:
            return {"ok": False, "error": str(exc), "hwnd": hwnd}
        return self._move_or_resize(hwnd, x, y, None, None, {"x": x, "y": y})

    def resize_window(self, hwnd: int, width: int, height: int) -> dict[str, Any]:
        try:
            width, height = validate_size(width, height)
        except GeometryRejected as exc:
            return {"ok": False, "error": str(exc), "hwnd": hwnd}
        return self._move_or_resize(
            hwnd, None, None, width, height, {"width": width, "height": height}
        )

    def _show_window(self, hwnd: int, sw_const: str, expected: str) -> dict[str, Any]:
        if not self._win32gui:
            return {"ok": False, "error": "win32gui unavailable", "hwnd": hwnd}
        if self.window_geometry(hwnd) is None:
            return {"ok": False, "error": f"window {hwnd} not found", "hwnd": hwnd}
        try:
            import win32con  # type: ignore

            self._win32gui.ShowWindow(hwnd, getattr(win32con, sw_const))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "hwnd": hwnd}
        observed, why_unknown = self._window_state(hwnd)
        result: dict[str, Any] = {
            "ok": observed == expected,
            "hwnd": hwnd,
            "state": observed,
            "requested_state": expected,
            # An unreadable state is not a verified one. Reporting `verified:
            # true` next to `state: null` would be claiming a confirmation that
            # never happened.
            "verified": observed is not None,
            "geometry": self.window_geometry(hwnd),
        }
        if observed is None:
            result["error"] = f"could not determine window state: {why_unknown}"
        elif observed != expected:
            result["error"] = f"window is {observed!r}, not {expected!r}"
        return result

    def minimize_window(self, hwnd: int) -> dict[str, Any]:
        return self._show_window(hwnd, "SW_MINIMIZE", "minimized")

    def maximize_window(self, hwnd: int) -> dict[str, Any]:
        return self._show_window(hwnd, "SW_MAXIMIZE", "maximized")

    def restore_window(self, hwnd: int) -> dict[str, Any]:
        return self._show_window(hwnd, "SW_RESTORE", "normal")

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
        # BREAKING, and the same change as LinuxBackend: an unknown button used
        # to fall through to left, so a typo produced a left click reported as
        # the button the caller named. Both `.get(button, LEFT...)` defaults
        # below are now unreachable for an invalid name.
        try:
            button = validate_button(button)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "x": x, "y": y, "button": button}
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
        try:
            button = validate_button(button)
            steps = validate_steps(steps)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "button": button}
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

    # ------------------------------------------------------------------
    # Input: the rest of the primitives (parity with LinuxBackend)
    #
    # Same limit as there: a keystroke or click has no readback. Once SendInput
    # accepts the event it belongs to whatever window has focus, and nothing
    # reports what that window did with it. So `ok` means SendInput accepted it
    # — checked against the count it returns, not assumed.
    # ------------------------------------------------------------------
    def pointer_position(self) -> dict[str, int] | None:
        """Where the pointer actually is, or None when it cannot be read."""
        try:
            si = _sendinput_structs()
            point = si["wintypes"].POINT()
            if not si["user32"].GetCursorPos(si["ctypes"].byref(point)):
                return None
            return {"x": int(point.x), "y": int(point.y)}
        except Exception:  # noqa: BLE001
            return None

    def double_click(self, x: int, y: int, button: str = "left") -> dict[str, Any]:
        try:
            button = validate_button(button)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "x": x, "y": y, "button": button}
        moved = self.mouse_move(x, y)
        if not moved.get("ok"):
            return {**moved, "ok": False}
        try:
            si = _sendinput_structs()
            down, up = {
                "left": (si["MOUSEEVENTF_LEFTDOWN"], si["MOUSEEVENTF_LEFTUP"]),
                "right": (si["MOUSEEVENTF_RIGHTDOWN"], si["MOUSEEVENTF_RIGHTUP"]),
                "middle": (si["MOUSEEVENTF_MIDDLEDOWN"], si["MOUSEEVENTF_MIDDLEUP"]),
            }[button]
            # All four events in ONE SendInput call. Two separate calls can fall
            # outside GetDoubleClickTime, and then the application sees two
            # single clicks — a different gesture from the one that was asked for.
            inputs = (si["INPUT"] * 4)()
            for i, flag in enumerate((down, up, down, up)):
                inputs[i].type = si["INPUT_MOUSE"]
                inputs[i].union.mi = si["MOUSEINPUT"](0, 0, 0, flag, 0, 0)
            sent = si["user32"].SendInput(
                4, si["ctypes"].byref(inputs), si["ctypes"].sizeof(si["INPUT"])
            )
            if sent != 4:
                return {"ok": False, "error": f"SendInput accepted {sent} of 4 events",
                        "x": x, "y": y, "button": button}
            return {"ok": True, "x": int(x), "y": int(y), "button": button, "clicks": 2,
                    "method": "SendInput"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "x": x, "y": y, "button": button}

    def scroll(self, direction: str = "down", amount: int = 3,
               x: int | None = None, y: int | None = None) -> dict[str, Any]:
        try:
            direction, amount = validate_scroll(direction, amount)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "direction": direction, "amount": amount}
        if x is not None and y is not None:
            moved = self.mouse_move(x, y)
            if not moved.get("ok"):
                return {**moved, "ok": False}
        try:
            si = _sendinput_structs()
            delta = si["WHEEL_DELTA"] * amount
            # Unlike X11, where a scroll is button 4/5/6/7, Windows sends one
            # wheel event carrying a signed delta. Down and left are negative.
            horizontal = direction in ("left", "right")
            if direction in ("down", "left"):
                delta = -delta
            flag = si["MOUSEEVENTF_HWHEEL"] if horizontal else si["MOUSEEVENTF_WHEEL"]
            inp = si["INPUT"]()
            inp.type = si["INPUT_MOUSE"]
            inp.union.mi = si["MOUSEINPUT"](0, 0, delta, flag, 0, 0)
            sent = si["user32"].SendInput(
                1, si["ctypes"].byref(inp), si["ctypes"].sizeof(si["INPUT"])
            )
            if sent != 1:
                return {"ok": False, "error": "SendInput wheel event rejected",
                        "direction": direction, "amount": amount}
            return {"ok": True, "direction": direction, "amount": amount, "delta": delta,
                    "method": "SendInput"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "direction": direction, "amount": amount}

    def key_down(self, key: str) -> dict[str, Any]:
        """Press and HOLD. The matching key_up is the caller's responsibility."""
        return self._key_transition(key, key_up=False)

    def key_up(self, key: str) -> dict[str, Any]:
        return self._key_transition(key, key_up=True)

    def _key_transition(self, key: str, *, key_up: bool) -> dict[str, Any]:
        try:
            key = validate_key(key)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "key": key}
        vk = _vk_for_key(key)
        if vk is None:
            # Deliberately NOT falling back to the unicode path used by
            # key_press: a held key must be a real virtual key, or there is
            # nothing for key_up to release.
            return {"ok": False, "error": f"unknown key: {key}", "key": key}
        try:
            if not self._send_vk(vk, key_up=key_up):
                return {"ok": False, "error": "SendInput rejected the key event", "key": key}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "key": key}
        return {"ok": True, "key": key, "state": "up" if key_up else "down",
                "method": "SendInput"}

    def hotkey(self, keys: list[str]) -> dict[str, Any]:
        """A chord — all keys down in order, then released in reverse."""
        try:
            keys = validate_hotkey(keys)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "keys": keys}
        vks = []
        for k in keys:
            vk = _vk_for_key(k)
            if vk is None:
                return {"ok": False, "error": f"unknown key: {k}", "keys": keys}
            vks.append((k, vk))

        pressed: list[int] = []
        try:
            for name, vk in vks:
                if not self._send_vk(vk, key_up=False):
                    return {"ok": False, "error": f"SendInput down failed for {name}",
                            "keys": keys}
                pressed.append(vk)
            return {"ok": True, "keys": keys, "chord": "+".join(keys), "method": "SendInput"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "keys": keys}
        finally:
            # Release in reverse, and release even on failure. A modifier left
            # stuck down turns every later keystroke into a shortcut — the
            # failure must not also leave the keyboard unusable.
            for vk in reversed(pressed):
                try:
                    self._send_vk(vk, key_up=True)
                except Exception:  # noqa: BLE001, S110
                    pass

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

    # ------------------------------------------------------------------
    # Network probes: routes, DNS, ping
    # ------------------------------------------------------------------
    def list_routes(self) -> list[dict[str, Any]]:
        """The IPv4 routing table via `route print -4`, parsed positionally.

        Linux reads /proc; Windows has no equivalent file, so this parses the
        one table `route` prints. The parser keys on the four-column numeric
        shape rather than on the header text, which is localised — a machine in
        Italian prints "Route attive" and a header match would return nothing
        while reporting no error.
        """
        exe = shutil.which("route")
        if not exe:
            return []
        try:
            r = subprocess.run(  # noqa: S603
                [exe, "print", "-4"],
                capture_output=True, text=True, timeout=15, check=False,
            )
        except Exception:  # noqa: BLE001
            return []

        out: list[dict[str, Any]] = []
        for line in (r.stdout or "").splitlines():
            fields = line.split()
            # destination netmask gateway interface metric
            if len(fields) != 5:
                continue
            destination, netmask, gateway, interface, metric = fields
            if not _looks_like_ipv4(destination) or not _looks_like_ipv4(netmask):
                continue
            try:
                metric_value = int(metric)
            except ValueError:
                continue
            out.append({
                "interface": interface,
                "destination": destination,
                # "On-link" is what Windows prints for a directly attached
                # network. Normalised to the same 0.0.0.0 Linux reports, so a
                # caller does not need a per-platform branch to read the field.
                "gateway": "0.0.0.0" if gateway.lower() == "on-link" else gateway,
                "netmask": netmask,
                "metric": metric_value,
                "default": destination == "0.0.0.0" and netmask == "0.0.0.0",
                "up": True,
                "source": "route print",
            })
        return out

    def dns_resolve(self, host: str) -> dict[str, Any]:
        return _dns.resolve(host)

    def dns_reverse(self, address: str) -> dict[str, Any]:
        return _dns.reverse(address)

    def ping(self, host: str, count: int = 2, timeout: int = 2) -> dict[str, Any]:
        """ICMP echo via the Windows `ping`, as argv and bounded.

        The flags differ from Linux: `-n` is the count and `-w` is a per-reply
        timeout in **milliseconds**, not seconds. Passing the Linux flags here
        would make `-W 2` a 2ms timeout, which fails against anything but
        loopback and looks like a network fault rather than a bug.
        """
        try:
            host = validate_host(host)
            count, timeout = validate_ping(count, timeout)
        except NetworkRejected as exc:
            return {"ok": False, "error": str(exc), "host": host}
        exe = shutil.which("ping")
        if not exe:
            return {"ok": False, "error": "ping binary not found", "host": host,
                    "available": False}
        try:
            r = subprocess.run(  # noqa: S603
                [exe, "-n", str(count), "-w", str(timeout * 1000), host],
                capture_output=True, text=True,
                timeout=count * timeout + 5, check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "ping did not finish within its own limits",
                    "host": host, "count": count}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "host": host}

        return {
            "ok": r.returncode == 0,
            "host": host,
            "transmitted": count,
            "received": _parse_ping_received_windows(r.stdout),
            "exit_code": r.returncode,
            "output": r.stdout[:4000],
        }

    # Service states, as the Service Control Manager reports them. Mapped to the
    # same vocabulary LinuxBackend uses for systemd, so one caller can read both.
    _SERVICE_STATES = {
        1: "stopped",
        2: "starting",
        3: "stopping",
        4: "running",
        5: "continuing",
        6: "pausing",
        7: "paused",
    }

    def list_services(self) -> list[dict[str, Any]]:
        """Enumerate real Windows services via the Service Control Manager.

        This used to return a single hardcoded entry:

            [{"name": "WinOsApi", "status": "unknown", "note": "use pywin32..."}]

        That service does not exist. A fabricated row is worse than an empty
        list — an empty list is merely uninformative, while a made-up one is an
        answer a caller can act on and be wrong. It is gone.

        Enumeration needs only `SC_MANAGER_ENUMERATE_SERVICE`, which an
        unprivileged user has; nothing here starts, stops or opens a service.
        """
        if not self._win32service:
            return []
        svc = self._win32service
        handle = None
        try:
            handle = svc.OpenSCManager(None, None, svc.SC_MANAGER_ENUMERATE_SERVICE)
            rows = svc.EnumServicesStatus(
                handle, svc.SERVICE_WIN32, svc.SERVICE_STATE_ALL
            )
        except Exception as exc:  # noqa: BLE001
            # Dichiarato, non trasformato in "nessun servizio": rispondere []
            # a un errore del Service Control Manager affermerebbe che questa
            # macchina non ha servizi. Il chiamante vede DISCOVERY_FAILED.
            raise DiscoveryFailed(f"EnumServicesStatus fallita: {exc}") from exc
        finally:
            if handle is not None:
                try:
                    svc.CloseServiceHandle(handle)
                except Exception:  # noqa: BLE001
                    pass

        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                short_name, display_name, status = row[0], row[1], row[2]
                state_code = int(status[1])
            except (IndexError, TypeError, ValueError):
                continue
            out.append({
                "name": short_name,
                "display_name": display_name,
                # Unknown codes keep their number rather than becoming
                # "unknown": a caller can look up 9 where it cannot look up a word.
                "status": self._SERVICE_STATES.get(state_code, f"state_{state_code}"),
                "scope": "system",
            })
        return out

    def control_service(self, name: str, action: str) -> dict[str, Any]:
        return {"ok": False, "error": "service control requires elevated pywin32", "name": name, "action": action}

    def audio_devices(self) -> list[dict[str, Any]]:
        return []

    def audio_volume(self) -> dict[str, Any]:
        return {"volume": None, "muted": None}

    def list_devices(self) -> list[dict[str, Any]]:
        """Storage volumes, in the same shape LinuxBackend reports for `/sys/block`.

        Deliberately NOT every PnP device. `list_devices` on Linux means block
        devices, and having one endpoint mean "disks" on one platform and
        "everything with a driver" on the other would be a worse defect than
        the empty list this replaces: the caller could not write one piece of
        code against it.

        `status` is measured, not asserted — `GetDriveType` says what kind of
        volume it is, and a drive letter that no longer resolves is reported as
        such rather than as "ok".
        """
        if not self._win32api:
            return []
        try:
            raw = self._win32api.GetLogicalDriveStrings()
        except Exception as exc:  # noqa: BLE001
            raise DiscoveryFailed(f"GetLogicalDriveStrings fallita: {exc}") from exc

        kinds = {
            0: "unknown",
            1: "no_root_dir",
            2: "removable",
            3: "fixed",
            4: "remote",
            5: "cdrom",
            6: "ramdisk",
        }
        out: list[dict[str, Any]] = []
        for root in [d for d in raw.split("\x00") if d]:
            try:
                code = int(self._win32api.GetDriveType(root))
            except Exception:  # noqa: BLE001
                code = 0
            out.append({
                "id": root.rstrip("\\"),
                "name": root.rstrip("\\"),
                "type": "block",
                "media": kinds.get(code, f"type_{code}"),
                # "present" is the claim the enumeration actually supports: the
                # volume is mounted. It is not a health check, and does not
                # pretend to be one.
                "status": "no_root_dir" if code == 1 else "present",
            })
        return out

    def list_printers(self) -> list[dict[str, Any]]:
        """Real printers via the spooler, local and connected.

        `GetDefaultPrinter` raises when no default is set — which is a normal
        state on a machine with no printers, not an error, so it is caught and
        turned into "no printer is default" rather than into no answer at all.
        """
        if not self._win32print:
            return []
        wp = self._win32print
        try:
            level = 2
            flags = wp.PRINTER_ENUM_LOCAL | wp.PRINTER_ENUM_CONNECTIONS
            rows = wp.EnumPrinters(flags, None, level)
        except Exception as exc:  # noqa: BLE001
            raise DiscoveryFailed(f"EnumPrinters fallita: {exc}") from exc

        try:
            default = wp.GetDefaultPrinter()
        except Exception:  # noqa: BLE001
            default = ""

        out: list[dict[str, Any]] = []
        for row in rows:
            info = row if isinstance(row, dict) else {}
            name = info.get("pPrinterName") or ""
            if not name:
                continue
            out.append({
                "name": name,
                "port": info.get("pPortName") or "",
                "driver": info.get("pDriverName") or "",
                # The spooler's status word is a bitmask; 0 means "no problem
                # reported", which is what "idle" means here and nothing more.
                "status": "idle" if not info.get("Status") else f"status_{info.get('Status')}",
                "jobs": int(info.get("cJobs") or 0),
                "default": name == default,
            })
        return out

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
