"""FakeBackend — full hard Linux E2E backend with realistic fixtures."""
from __future__ import annotations

import base64
import os
import platform
import time
from pathlib import Path
from typing import Any

# Minimal 1x1 PNG
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

CRM_UI_TREE: dict[str, Any] = {
    "hwnd": 1001,
    "name": "Contoso CRM",
    "control_type": "Window",
    "automation_id": "ContosoCRM.Main",
    "children": [
        {
            "name": "MenuBar",
            "control_type": "MenuBar",
            "automation_id": "main.menu",
            "children": [
                {"name": "File", "control_type": "MenuItem", "automation_id": "menu.file", "children": [
                    {"name": "New Customer", "control_type": "MenuItem", "automation_id": "menu.file.new_customer", "children": []},
                    {"name": "Export", "control_type": "MenuItem", "automation_id": "menu.file.export", "children": []},
                    {"name": "Exit", "control_type": "MenuItem", "automation_id": "menu.file.exit", "children": []},
                ]},
                {"name": "Customers", "control_type": "MenuItem", "automation_id": "menu.customers", "children": []},
                {"name": "Reports", "control_type": "MenuItem", "automation_id": "menu.reports", "children": []},
            ],
        },
        {
            "name": "CustomerPanel",
            "control_type": "Pane",
            "automation_id": "panel.customer",
            "children": [
                {"name": "Customer Name", "control_type": "Edit", "automation_id": "field.customer_name", "value": "", "children": []},
                {"name": "Email", "control_type": "Edit", "automation_id": "field.email", "value": "", "children": []},
                {"name": "Phone", "control_type": "Edit", "automation_id": "field.phone", "value": "", "children": []},
                {"name": "Save", "control_type": "Button", "automation_id": "btn.save", "children": []},
                {"name": "Cancel", "control_type": "Button", "automation_id": "btn.cancel", "children": []},
                {"name": "Search", "control_type": "Button", "automation_id": "btn.search", "children": []},
            ],
        },
        {
            "name": "CustomerGrid",
            "control_type": "DataGrid",
            "automation_id": "grid.customers",
            "children": [
                {"name": "Row1", "control_type": "DataItem", "automation_id": "grid.row.1",
                 "value": "Alice Rossi|alice@contoso.it|+39 02 1234567", "children": []},
                {"name": "Row2", "control_type": "DataItem", "automation_id": "grid.row.2",
                 "value": "Bruno Bianchi|bruno@contoso.it|+39 06 7654321", "children": []},
            ],
        },
        {
            "name": "StatusBar",
            "control_type": "StatusBar",
            "automation_id": "status.main",
            "value": "Ready",
            "children": [],
        },
    ],
}


class FakeBackend:
    """Deterministic in-memory OS backend for Linux CI and local E2E."""

    name = "fake"

    def __init__(self, sandbox_root: str = "sandbox") -> None:
        self._start = time.time()
        self.sandbox = Path(sandbox_root).resolve()
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self._clipboard = ""
        self._mouse = {"x": 0, "y": 0}
        self._volume = 50
        self._processes: dict[int, dict[str, Any]] = {
            1: {"pid": 1, "name": "System", "status": "running", "cpu_percent": 0.1, "memory_mb": 8.0},
            42: {"pid": 42, "name": "ContosoCRM.exe", "status": "running", "cpu_percent": 2.5, "memory_mb": 128.0},
            100: {"pid": 100, "name": "notepad.exe", "status": "running", "cpu_percent": 0.0, "memory_mb": 16.0},
        }
        self._next_pid = 1000
        self._windows: dict[int, dict[str, Any]] = {
            1001: {"hwnd": 1001, "title": "Contoso CRM", "pid": 42, "visible": True, "focused": True,
                   "rect": {"x": 100, "y": 50, "w": 1024, "h": 768}},
            1002: {"hwnd": 1002, "title": "Untitled - Notepad", "pid": 100, "visible": True, "focused": False,
                   "rect": {"x": 200, "y": 100, "w": 600, "h": 400}},
        }
        self._services: dict[str, dict[str, Any]] = {
            "WinOsApi": {"name": "WinOsApi", "status": "running", "start_type": "auto"},
            "Spooler": {"name": "Spooler", "status": "running", "start_type": "auto"},
            "FakeSvc": {"name": "FakeSvc", "status": "stopped", "start_type": "manual"},
        }
        self._registry: dict[str, dict[str, Any]] = {
            r"HKLM\SOFTWARE\WinOsApi": {"InstallPath": "C:\\Program Files\\WinOsApi", "Version": "1.0.0"},
            r"HKCU\Software\ContosoCRM": {"LastUser": "alice", "Theme": "dark"},
        }
        self._input_log: list[dict[str, Any]] = []
        self._terminal_log: list[dict[str, Any]] = []

    # --- System ---
    def get_system_info(self) -> dict[str, Any]:
        return {
            "hostname": "fake-win-host",
            "os": "Windows",
            "os_version": "10.0.19045 (Fake)",
            "architecture": platform.machine() or "x86_64",
            "backend": self.name,
            "python": platform.python_version(),
            "platform_real": platform.system(),
        }

    def get_resources(self) -> dict[str, Any]:
        return {
            "cpu_percent": 12.5,
            "memory": {"total_mb": 16384, "used_mb": 6144, "percent": 37.5},
            "disk": {"total_gb": 512, "used_gb": 200, "percent": 39.0},
        }

    def get_uptime(self) -> dict[str, Any]:
        secs = time.time() - self._start
        return {"uptime_seconds": round(secs, 2), "boot_time": self._start}

    def power_action(self, action: str) -> dict[str, Any]:
        allowed = {"sleep", "hibernate", "shutdown", "reboot", "lock"}
        if action not in allowed:
            return {"ok": False, "error": f"unknown action: {action}"}
        return {"ok": True, "action": action, "simulated": True}

    # --- Processes ---
    def list_processes(self) -> list[dict[str, Any]]:
        return list(self._processes.values())

    def get_process(self, pid: int) -> dict[str, Any] | None:
        return self._processes.get(pid)

    def start_process(self, command: str, args: list[str] | None = None) -> dict[str, Any]:
        self._next_pid += 1
        pid = self._next_pid
        name = Path(command).name or command
        proc = {"pid": pid, "name": name, "status": "running", "cpu_percent": 0.0, "memory_mb": 10.0,
                "command": command, "args": args or []}
        self._processes[pid] = proc
        return proc

    def terminate_process(self, pid: int) -> dict[str, Any]:
        if pid not in self._processes:
            return {"ok": False, "error": "not found", "pid": pid}
        self._processes[pid]["status"] = "terminated"
        return {"ok": True, "pid": pid, "status": "terminated"}

    # --- Apps ---
    def discover_apps(self) -> list[dict[str, Any]]:
        return [
            {"id": "contoso-crm", "name": "Contoso CRM", "path": r"C:\Program Files\Contoso\CRM.exe",
             "version": "3.2.1", "publisher": "Contoso SpA", "source": "fake"},
            {"id": "notepad", "name": "Notepad", "path": r"C:\Windows\System32\notepad.exe",
             "version": "10.0", "publisher": "Microsoft", "source": "fake"},
            {"id": "calc", "name": "Calculator", "path": r"C:\Windows\System32\calc.exe",
             "version": "10.0", "publisher": "Microsoft", "source": "fake"},
            {"id": "excel", "name": "Microsoft Excel", "path": r"C:\Program Files\Microsoft Office\EXCEL.EXE",
             "version": "16.0", "publisher": "Microsoft", "source": "fake"},
        ]

    # --- Windows ---
    def list_windows(self) -> list[dict[str, Any]]:
        return list(self._windows.values())

    def get_window(self, hwnd: int) -> dict[str, Any] | None:
        return self._windows.get(hwnd)

    def focus_window(self, hwnd: int) -> dict[str, Any]:
        if hwnd not in self._windows:
            return {"ok": False, "error": "not found"}
        for w in self._windows.values():
            w["focused"] = False
        self._windows[hwnd]["focused"] = True
        return {"ok": True, "hwnd": hwnd}

    def close_window(self, hwnd: int) -> dict[str, Any]:
        if hwnd not in self._windows:
            return {"ok": False, "error": "not found"}
        del self._windows[hwnd]
        return {"ok": True, "hwnd": hwnd}

    # --- UI ---
    def get_ui_tree(self, hwnd: int | None = None) -> dict[str, Any]:
        if hwnd is None or hwnd == 1001:
            return dict(CRM_UI_TREE)
        win = self._windows.get(hwnd or 0)
        return {
            "hwnd": hwnd,
            "name": (win or {}).get("title", "Unknown"),
            "control_type": "Window",
            "children": [],
        }

    # --- Input ---
    def mouse_move(self, x: int, y: int) -> dict[str, Any]:
        self._mouse = {"x": x, "y": y}
        self._input_log.append({"type": "mouse_move", "x": x, "y": y})
        return {"ok": True, **self._mouse}

    def mouse_click(self, x: int, y: int, button: str = "left") -> dict[str, Any]:
        self._mouse = {"x": x, "y": y}
        self._input_log.append({"type": "mouse_click", "x": x, "y": y, "button": button})
        return {"ok": True, "x": x, "y": y, "button": button}

    def key_press(self, key: str, modifiers: list[str] | None = None) -> dict[str, Any]:
        entry = {"type": "key_press", "key": key, "modifiers": modifiers or []}
        self._input_log.append(entry)
        return {"ok": True, **entry}

    def type_text(self, text: str) -> dict[str, Any]:
        self._input_log.append({"type": "type_text", "text": text})
        return {"ok": True, "length": len(text)}

    # --- Clipboard ---
    def clipboard_get(self) -> dict[str, Any]:
        return {"text": self._clipboard, "format": "text"}

    def clipboard_set(self, text: str) -> dict[str, Any]:
        self._clipboard = text
        return {"ok": True, "length": len(text)}

    # --- Display ---
    def list_displays(self) -> list[dict[str, Any]]:
        return [
            {"id": 0, "name": "Fake Display 1", "width": 1920, "height": 1080, "primary": True, "scale": 1.0},
            {"id": 1, "name": "Fake Display 2", "width": 1280, "height": 720, "primary": False, "scale": 1.0},
        ]

    def screenshot(self, display_id: int | None = None) -> dict[str, Any]:
        return {
            "ok": True,
            "display_id": display_id or 0,
            "format": "png",
            "width": 1,
            "height": 1,
            "data_base64": base64.b64encode(_PNG_1X1).decode("ascii"),
        }

    # --- Filesystem (sandboxed) ---
    def _safe_path(self, path: str) -> Path:
        # Normalize Windows separators; reject parent refs early
        norm = path.replace(chr(92), "/")
        parts = [x for x in norm.split("/") if x not in ("", ".")]
        if ".." in parts or norm.startswith("/") or (len(norm) >= 2 and norm[1] == ":"):
            raise PermissionError(f"Path outside sandbox: {path}")
        p = Path(norm)
        if not p.is_absolute():
            p = self.sandbox / p
        resolved = p.resolve()
        try:
            resolved.relative_to(self.sandbox)
        except ValueError as e:
            raise PermissionError(f"Path outside sandbox: {path}") from e
        return resolved

    def fs_list(self, path: str) -> list[dict[str, Any]]:
        target = self._safe_path(path) if path not in (".", "") else self.sandbox
        if not target.exists():
            return []
        if not target.is_dir():
            raise NotADirectoryError(str(target))
        out = []
        for child in sorted(target.iterdir()):
            out.append({
                "name": child.name,
                "path": str(child.relative_to(self.sandbox)),
                "is_dir": child.is_dir(),
                "size": child.stat().st_size if child.is_file() else 0,
            })
        return out

    def fs_read(self, path: str, max_bytes: int = 65536) -> dict[str, Any]:
        target = self._safe_path(path)
        data = target.read_bytes()[:max_bytes]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        return {"path": path, "size": len(data), "text": text, "base64": base64.b64encode(data).decode()}

    def fs_write(self, path: str, content: str) -> dict[str, Any]:
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": path, "bytes": len(content.encode())}

    def fs_delete(self, path: str) -> dict[str, Any]:
        target = self._safe_path(path)
        if target.is_dir():
            target.rmdir()
        else:
            target.unlink(missing_ok=True)
        return {"ok": True, "path": path}

    # --- Storage ---
    def list_drives(self) -> list[dict[str, Any]]:
        return [
            {"letter": "C:", "label": "System", "fs": "NTFS", "total_gb": 512, "free_gb": 312},
            {"letter": "D:", "label": "Data", "fs": "NTFS", "total_gb": 1024, "free_gb": 800},
        ]

    # --- Network ---
    def network_interfaces(self) -> list[dict[str, Any]]:
        return [
            {"name": "Ethernet", "addresses": ["10.0.0.5"], "mac": "00:11:22:33:44:55", "up": True},
            {"name": "Wi-Fi", "addresses": ["192.168.1.10"], "mac": "AA:BB:CC:DD:EE:FF", "up": True},
        ]

    def network_connections(self) -> list[dict[str, Any]]:
        return [
            {"local": "10.0.0.5:8765", "remote": "127.0.0.1:54321", "status": "LISTEN", "pid": 42},
        ]

    # --- Services ---
    def list_services(self) -> list[dict[str, Any]]:
        return list(self._services.values())

    def control_service(self, name: str, action: str) -> dict[str, Any]:
        if name not in self._services:
            return {"ok": False, "error": "not found"}
        svc = self._services[name]
        if action == "start":
            svc["status"] = "running"
        elif action == "stop":
            svc["status"] = "stopped"
        elif action == "restart":
            svc["status"] = "running"
        else:
            return {"ok": False, "error": f"unknown action: {action}"}
        return {"ok": True, "service": dict(svc)}

    # --- Audio ---
    def audio_devices(self) -> list[dict[str, Any]]:
        return [
            {"id": "speakers", "name": "Fake Speakers", "type": "output", "default": True},
            {"id": "mic", "name": "Fake Microphone", "type": "input", "default": True},
        ]

    def audio_volume(self) -> dict[str, Any]:
        return {"volume": self._volume, "muted": False}

    # --- Devices ---
    def list_devices(self) -> list[dict[str, Any]]:
        return [
            {"id": "usb-1", "name": "Fake USB Disk", "type": "storage", "status": "ok"},
            {"id": "hid-1", "name": "Fake Keyboard", "type": "hid", "status": "ok"},
        ]

    # --- Printers ---
    def list_printers(self) -> list[dict[str, Any]]:
        return [
            {"name": "Fake PDF Printer", "status": "idle", "default": True},
            {"name": "Office Laser", "status": "idle", "default": False},
        ]

    # --- Users / sessions ---
    def list_users(self) -> list[dict[str, Any]]:
        return [
            {"username": "alice", "domain": "FAKE", "admin": True},
            {"username": "bob", "domain": "FAKE", "admin": False},
        ]

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {"id": 1, "user": "alice", "state": "Active", "client": "Console"},
        ]

    # --- Registry ---
    def registry_read(self, path: str, name: str | None = None) -> dict[str, Any]:
        key = self._registry.get(path)
        if key is None:
            return {"ok": False, "error": "key not found", "path": path}
        if name is None:
            return {"ok": True, "path": path, "values": dict(key)}
        if name not in key:
            return {"ok": False, "error": "value not found", "path": path, "name": name}
        return {"ok": True, "path": path, "name": name, "value": key[name]}

    def registry_write(self, path: str, name: str, value: Any) -> dict[str, Any]:
        if path not in self._registry:
            self._registry[path] = {}
        self._registry[path][name] = value
        return {"ok": True, "path": path, "name": name, "value": value}

    # --- Terminal ---
    def terminal_execute(self, command: str, policy: str = "ALLOW") -> dict[str, Any]:
        policy = policy.upper()
        entry = {"command": command, "policy": policy, "ts": time.time()}
        self._terminal_log.append(entry)
        if policy == "DENY":
            return {"ok": False, "policy": "DENY", "error": "command denied by policy", "command": command}
        if policy == "ADMIN":
            return {"ok": True, "policy": "ADMIN", "stdout": f"[admin] executed: {command}", "stderr": "", "exit_code": 0}
        if policy != "ALLOW":
            return {"ok": False, "error": f"unknown policy: {policy}"}
        # Safe fake execution — never shell out to real OS for arbitrary commands
        return {"ok": True, "policy": "ALLOW", "stdout": f"fake output for: {command}", "stderr": "", "exit_code": 0}
