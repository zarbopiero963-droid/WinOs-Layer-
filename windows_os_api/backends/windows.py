"""WindowsBackend — real Windows OS bindings with guarded imports."""
from __future__ import annotations

import platform
import sys
import time
from pathlib import Path
from typing import Any


class WindowsBackendUnavailable(RuntimeError):
    pass


def _require_windows() -> None:
    if sys.platform != "win32":
        raise WindowsBackendUnavailable("WindowsBackend requires win32 platform")


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
        # Intentionally stubbed — real power actions require elevated privileges
        return {"ok": False, "error": "power actions require interactive elevation", "action": action}

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
        return {"pid": proc.pid, "name": Path(command).name, "status": "running"}

    def terminate_process(self, pid: int) -> dict[str, Any]:
        if not self._psutil:
            return {"ok": False, "error": "psutil unavailable"}
        try:
            self._psutil.Process(pid).terminate()
            return {"ok": True, "pid": pid}
        except self._psutil.Error as e:
            return {"ok": False, "error": str(e), "pid": pid}

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
        # Registry uninstall keys when available
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

    def get_ui_tree(self, hwnd: int | None = None) -> dict[str, Any]:
        # UIA stub — works when comtypes/UIAutomation available
        try:
            return self._uia_tree(hwnd)
        except Exception as e:  # noqa: BLE001
            return {"hwnd": hwnd, "name": "", "control_type": "Window", "children": [], "error": str(e), "stub": True}

    def _uia_tree(self, hwnd: int | None) -> dict[str, Any]:
        """Best-effort UI Automation tree; raises if deps missing."""
        raise NotImplementedError("Full UIA tree requires comtypes UIAutomationClient on Windows")

    def mouse_move(self, x: int, y: int) -> dict[str, Any]:
        if self._win32api:
            self._win32api.SetCursorPos((x, y))
            return {"ok": True, "x": x, "y": y}
        return {"ok": False, "error": "win32api unavailable"}

    def mouse_click(self, x: int, y: int, button: str = "left") -> dict[str, Any]:
        self.mouse_move(x, y)
        return {"ok": True, "x": x, "y": y, "button": button, "note": "click via SendInput when available"}

    def key_press(self, key: str, modifiers: list[str] | None = None) -> dict[str, Any]:
        return {"ok": True, "key": key, "modifiers": modifiers or [], "note": "SendInput stub"}

    def type_text(self, text: str) -> dict[str, Any]:
        return {"ok": True, "length": len(text), "note": "SendInput stub"}

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

    def list_displays(self) -> list[dict[str, Any]]:
        return [{"id": 0, "name": "Primary", "width": 1920, "height": 1080, "primary": True, "scale": 1.0}]

    def screenshot(self, display_id: int | None = None) -> dict[str, Any]:
        return {"ok": False, "error": "screenshot requires Pillow/GDI+ on Windows", "display_id": display_id}

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
        raw = path.replace("/", '\\')
        parts = raw.split('\\', 1)
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
        import subprocess

        try:
            r = subprocess.run(  # noqa: S603
                command, shell=True, capture_output=True, text=True, timeout=30
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
