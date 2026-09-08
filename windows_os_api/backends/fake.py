"""FakeBackend — full hard Linux E2E backend with realistic fixtures."""
from __future__ import annotations

import base64
import os
import platform
import time
from pathlib import Path
from typing import Any

from windows_os_api.os.terminal.allowlist import CommandRejected, resolve as resolve_command
from windows_os_api.os.windows.geometry import (
    GeometryRejected,
    validate_position,
    validate_size,
)
from windows_os_api.os.input.validation import (
    InputRejected,
    validate_button,
    validate_hotkey,
    validate_key,
    validate_scroll,
    validate_steps,
)
from windows_os_api.os.network.validation import (
    NetworkRejected,
    validate_host,
    validate_ip,
    validate_ping,
)

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

    # --- Window geometry / state ---
    #
    # The fake applies the SAME validation as the real backends, and — this part
    # matters more — it does NOT hand back exactly what it was asked for. A
    # window manager adds a frame offset: measured under Xvfb + openbox, a move
    # to (300, 200) lands at (302, 240). If the fake returned the request
    # verbatim, a test written against it could assert `geometry == requested`,
    # pass here, and be wrong on both real platforms. A fake that is easier than
    # production is a fake that certifies the wrong thing.
    _FRAME_OFFSET = (2, 40)

    def window_geometry(self, hwnd: int) -> dict[str, int] | None:
        win = self._windows.get(hwnd)
        if win is None:
            return None
        rect = win["rect"]
        return {"x": rect["x"], "y": rect["y"], "width": rect["w"], "height": rect["h"]}

    def move_window(self, hwnd: int, x: int, y: int) -> dict[str, Any]:
        try:
            x, y = validate_position(x, y)
        except GeometryRejected as exc:
            return {"ok": False, "error": str(exc), "hwnd": hwnd}
        before = self.window_geometry(hwnd)
        if before is None:
            return {"ok": False, "error": f"window {hwnd} not found", "hwnd": hwnd}
        dx, dy = self._FRAME_OFFSET
        rect = self._windows[hwnd]["rect"]
        rect["x"], rect["y"] = x + dx, y + dy
        return {"ok": True, "hwnd": hwnd, "requested": {"x": x, "y": y},
                "geometry": self.window_geometry(hwnd), "previous": before}

    def resize_window(self, hwnd: int, width: int, height: int) -> dict[str, Any]:
        try:
            width, height = validate_size(width, height)
        except GeometryRejected as exc:
            return {"ok": False, "error": str(exc), "hwnd": hwnd}
        before = self.window_geometry(hwnd)
        if before is None:
            return {"ok": False, "error": f"window {hwnd} not found", "hwnd": hwnd}
        rect = self._windows[hwnd]["rect"]
        # Quantised to even numbers, the way a terminal snaps to character
        # cells: 700x500 came back as 700x498 on the real thing.
        rect["w"], rect["h"] = width - (width % 2), height - (height % 2)
        return {"ok": True, "hwnd": hwnd, "requested": {"width": width, "height": height},
                "geometry": self.window_geometry(hwnd), "previous": before}

    def _set_state(self, hwnd: int, state: str) -> dict[str, Any]:
        if hwnd not in self._windows:
            return {"ok": False, "error": f"window {hwnd} not found", "hwnd": hwnd}
        win = self._windows[hwnd]
        win["state"] = state
        win["visible"] = state != "minimized"
        return {"ok": True, "hwnd": hwnd, "state": state, "requested_state": state,
                "verified": True, "geometry": self.window_geometry(hwnd)}

    def minimize_window(self, hwnd: int) -> dict[str, Any]:
        return self._set_state(hwnd, "minimized")

    def maximize_window(self, hwnd: int) -> dict[str, Any]:
        return self._set_state(hwnd, "maximized")

    def restore_window(self, hwnd: int) -> dict[str, Any]:
        return self._set_state(hwnd, "normal")

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
    # The fake applies the SAME validation as the real backends. It has no OS to
    # refuse anything for it, so without this a test written against the fake
    # would believe a typo'd button or an out-of-range scroll is accepted — and
    # a fake that is more permissive than production certifies the wrong thing.
    def mouse_move(self, x: int, y: int) -> dict[str, Any]:
        try:
            x, y = validate_position(x, y)
        except GeometryRejected as exc:
            return {"ok": False, "error": str(exc), "x": x, "y": y}
        self._mouse = {"x": x, "y": y}
        self._input_log.append({"type": "mouse_move", "x": x, "y": y})
        return {"ok": True, **self._mouse, "position": dict(self._mouse)}

    def pointer_position(self) -> dict[str, int] | None:
        return dict(self._mouse)

    def mouse_click(self, x: int, y: int, button: str = "left") -> dict[str, Any]:
        try:
            button = validate_button(button)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "x": x, "y": y, "button": button}
        self._mouse = {"x": x, "y": y}
        self._input_log.append({"type": "mouse_click", "x": x, "y": y, "button": button})
        return {"ok": True, "x": x, "y": y, "button": button}

    def double_click(self, x: int, y: int, button: str = "left") -> dict[str, Any]:
        try:
            button = validate_button(button)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "x": x, "y": y, "button": button}
        self._mouse = {"x": x, "y": y}
        self._input_log.append(
            {"type": "double_click", "x": x, "y": y, "button": button, "clicks": 2}
        )
        return {"ok": True, "x": x, "y": y, "button": button, "clicks": 2}

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
        self._input_log.append({"type": "scroll", "direction": direction, "amount": amount})
        return {"ok": True, "direction": direction, "amount": amount}

    def key_down(self, key: str) -> dict[str, Any]:
        return self._key_transition(key, "down")

    def key_up(self, key: str) -> dict[str, Any]:
        return self._key_transition(key, "up")

    def _key_transition(self, key: str, state: str) -> dict[str, Any]:
        try:
            key = validate_key(key)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "key": key}
        self._input_log.append({"type": f"key_{state}", "key": key})
        return {"ok": True, "key": key, "state": state}

    def hotkey(self, keys: list[str]) -> dict[str, Any]:
        try:
            keys = validate_hotkey(keys)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "keys": keys}
        self._input_log.append({"type": "hotkey", "keys": list(keys)})
        return {"ok": True, "keys": keys, "chord": "+".join(keys)}

    def mouse_drag(self, x1: int, y1: int, x2: int, y2: int,
                   button: str = "left", *, steps: int = 10) -> dict[str, Any]:
        try:
            button = validate_button(button)
            steps = validate_steps(steps)
            x1, y1 = validate_position(x1, y1)
            x2, y2 = validate_position(x2, y2)
        except (InputRejected, GeometryRejected) as exc:
            return {"ok": False, "error": str(exc), "button": button}
        self._mouse = {"x": x2, "y": y2}
        self._input_log.append({
            "type": "mouse_drag", "from": {"x": x1, "y": y1},
            "to": {"x": x2, "y": y2}, "button": button, "steps": steps,
        })
        return {"ok": True, "from": {"x": x1, "y": y1}, "requested": {"x": x2, "y": y2},
                "position": dict(self._mouse), "button": button, "steps": steps}

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

    # --- Network probes ---
    #
    # The fake answers from fixtures — it must not touch the network, or the
    # suite stops being offline and deterministic. It applies the SAME
    # validation as the real backends, so a test written against it cannot come
    # to believe that a leading-hyphen host or an unbounded count is accepted.
    def list_routes(self) -> list[dict[str, Any]]:
        return [
            {"interface": "Ethernet", "destination": "0.0.0.0", "gateway": "10.0.0.1",
             "netmask": "0.0.0.0", "metric": 25, "default": True, "up": True,
             "source": "fake"},
            {"interface": "Ethernet", "destination": "10.0.0.0", "gateway": "0.0.0.0",
             "netmask": "255.255.255.0", "metric": 281, "default": False, "up": True,
             "source": "fake"},
        ]

    def dns_resolve(self, host: str) -> dict[str, Any]:
        try:
            host = validate_host(host)
        except NetworkRejected as exc:
            return {"ok": False, "error": str(exc), "host": host}
        known = {
            "localhost": ["127.0.0.1"],
            "contoso-crm.local": ["10.0.0.5"],
        }
        addresses = known.get(host.lower())
        if not addresses:
            return {"ok": False, "error": f"could not resolve {host!r}: not in fake DNS",
                    "host": host, "addresses": []}
        return {"ok": True, "host": host, "addresses": addresses,
                "ipv4": addresses, "ipv6": []}

    def dns_reverse(self, address: str) -> dict[str, Any]:
        try:
            address = validate_ip(address)
        except NetworkRejected as exc:
            return {"ok": False, "error": str(exc), "address": address}
        known = {"127.0.0.1": "localhost", "10.0.0.5": "contoso-crm.local"}
        hostname = known.get(address)
        if not hostname:
            return {"ok": False, "error": f"no reverse record for {address}",
                    "address": address}
        return {"ok": True, "address": address, "hostname": hostname,
                "aliases": [], "addresses": [address]}

    def ping(self, host: str, count: int = 2, timeout: int = 2) -> dict[str, Any]:
        try:
            host = validate_host(host)
            count, timeout = validate_ping(count, timeout)
        except NetworkRejected as exc:
            return {"ok": False, "error": str(exc), "host": host}
        reachable = host in ("127.0.0.1", "localhost", "10.0.0.5", "contoso-crm.local")
        return {
            "ok": reachable,
            "host": host,
            "transmitted": count,
            "received": count if reachable else 0,
            "exit_code": 0 if reachable else 1,
            "output": f"fake ping to {host}",
        }

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

    def audio_set_volume(self, percent: int) -> dict[str, Any]:
        self._volume = max(0, min(100, int(percent)))
        return {"ok": True, "volume": self._volume, "backend": "fake"}

    def audio_set_mute(self, muted: bool) -> dict[str, Any]:
        self._muted = bool(muted)
        return {"ok": True, "muted": self._muted, "backend": "fake"}

    def audio_volume(self) -> dict[str, Any]:
        return {"volume": self._volume, "muted": self._muted}

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

    def session_info(self) -> dict[str, Any]:
        return {
            "session": {"session_type": "fake", "wayland": False, "x11": False},
            "sessions": self.list_sessions(),
            "users": self.list_users(),
        }

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
            try:
                resolve_command(command, policy)
            except CommandRejected as exc:
                return {"ok": False, "policy": "DENY", "error": str(exc), "command": command}
            return {"ok": True, "policy": "ADMIN", "stdout": f"[admin] executed: {command}", "stderr": "", "exit_code": 0}
        if policy != "ALLOW":
            return {"ok": False, "error": f"unknown policy: {policy}"}
        # The fake backend never shells out, but it still honours the allowlist:
        # if it accepted commands the real backends reject, every test written
        # against it would give false confidence about production behaviour.
        try:
            resolve_command(command, policy)
        except CommandRejected as exc:
            return {"ok": False, "policy": "DENY", "error": str(exc), "command": command}
        return {"ok": True, "policy": "ALLOW", "stdout": f"fake output for: {command}", "stderr": "", "exit_code": 0}
