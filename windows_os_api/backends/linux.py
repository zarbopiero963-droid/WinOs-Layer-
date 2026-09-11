"""LinuxBackend — real Linux OS operations (parity surface with WindowsBackend).

NOT a Windows emulator. Process/FS/system/network use the live Linux host.
Window listing / AT-SPI / clipboard / audio / screenshots degrade gracefully
when optional tools/libraries are missing.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import time
from pathlib import Path
from typing import Any

from windows_os_api.os.network import dns as _dns
from windows_os_api.os.network.validation import (
    NetworkRejected,
    validate_host,
    validate_ping,
)

from windows_os_api.os.capability import DiscoveryFailed
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
    MOUSE_BUTTONS,
    SCROLL_BUTTONS,
    InputRejected,
    validate_button,
    validate_hotkey,
    validate_key,
    validate_scroll,
    validate_steps,
)

from windows_os_api.backends import linux_audio as _laudio
from windows_os_api.backends import linux_services as _lsvc
from windows_os_api.backends.linux_session import (
    detect_session,
    probe_wayland_capabilities,
    build_wayland_type_cmd,
    build_wayland_click_cmd,
    build_wayland_key_cmd,
    build_wayland_list_windows_cmd,
    wayland_input_tool,
    wayland_window_tool,
)
from windows_os_api.apps.vision.ocr import tesseract_available


class LinuxBackendUnavailable(RuntimeError):
    pass


def _require_linux() -> None:
    import sys

    if sys.platform == "win32":
        raise LinuxBackendUnavailable("LinuxBackend requires a non-Windows platform")


def _ensure_pyatspi():
    """Import pyatspi, adding Debian dist-packages if the venv is isolated."""
    try:
        import pyatspi  # type: ignore

        return pyatspi
    except Exception:
        pass
    import sys

    dist = "/usr/lib/python3/dist-packages"
    if dist not in sys.path:
        sys.path.insert(0, dist)
    import pyatspi  # type: ignore

    return pyatspi


def _hex_le_to_ip(hex_value: str) -> str:
    """`/proc/net/route` stores addresses as hex in host byte order.

    On x86 that is little-endian, so `010200C0` is 192.0.2.1 — the bytes read
    back to front. Verified against the live table before this was written.
    """
    return socket.inet_ntoa(struct.pack("<L", int(hex_value, 16)))


_PING_RECEIVED = re.compile(r"(\d+)\s+(?:packets\s+)?received", re.IGNORECASE)


def _parse_ping_received(output: str) -> int | None:
    """How many replies came back, or None when the summary cannot be read.

    None means "not parsed" and is reported as such rather than as 0, which
    would be an invented answer — and the wrong one, since exit code 0 already
    says at least one reply arrived.
    """
    match = _PING_RECEIVED.search(output or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _display_env() -> dict[str, str]:
    """Subprocess env that preserves DISPLAY / XAUTHORITY for X11 tools."""
    env = dict(os.environ)
    if not env.get("DISPLAY"):
        # Common agent/desktop default used by this project
        env["DISPLAY"] = ":2"
    return env


class LinuxBackend:
    """Real Linux backend using psutil, subprocess, pathlib, and optional tools."""

    name = "linux"

    def __init__(
        self,
        sandbox_root: str = "sandbox",
        allow_paths: list[str] | None = None,
        registry_path: str | None = None,
    ) -> None:
        _require_linux()
        self.sandbox = Path(sandbox_root).resolve()
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.allow_paths = [Path(p).resolve() for p in (allow_paths or [])]
        self._start = time.time()
        self._children: dict[int, subprocess.Popen] = {}
        self._psutil = None
        try:
            import psutil as _psutil

            self._psutil = _psutil
        except ImportError:
            pass

        if registry_path:
            self._registry_file = Path(registry_path)
        else:
            self._registry_file = Path.home() / ".config" / "winos-api" / "registry.json"
        self._registry_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._registry_file.exists():
            self._registry_file.write_text("{}", encoding="utf-8")

        self._caps = self._probe_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------
    def _probe_capabilities(self) -> dict[str, bool]:
        has_atspi = False
        try:
            _ensure_pyatspi()
            has_atspi = True
        except Exception:  # noqa: BLE001
            has_atspi = False
        has_mss = False
        try:
            import mss  # type: ignore  # noqa: F401

            has_mss = True
        except Exception:  # noqa: BLE001
            has_mss = False
        session = probe_wayland_capabilities()
        has_wm_x11 = bool(shutil.which("wmctrl") or shutil.which("xdotool"))
        has_wm = bool(session.get("windows_ui") or has_wm_x11)
        has_clip = bool(
            shutil.which("xclip")
            or shutil.which("xsel")
            or shutil.which("wl-copy")
            or shutil.which("wl-paste")
        )
        audio_tools = _laudio.audio_backend_available()
        has_audio = bool(audio_tools.get("pactl") or audio_tools.get("wpctl"))
        has_systemctl = bool(shutil.which("systemctl"))
        has_ocr = bool(tesseract_available() or True)  # template fallback always usable with Pillow
        # ocr_tesseract distinguishes accuracy class
        return {
            "processes": self._psutil is not None,
            "filesystem": True,
            "network": self._psutil is not None,
            "system": True,
            "windows_ui": has_wm,
            "atspi": has_atspi,
            "clipboard": has_clip,
            "screenshot": has_mss,
            "audio": has_audio,
            "services": has_systemctl,
            # Su Linux il controllo E' implementato: se systemctl manca e'
            # UNAVAILABLE (installabile), non NOT_SUPPORTED.
            "service_control": has_systemctl,
            "sessions": True,
            "devices": Path("/sys/block").is_dir(),
            "printers": bool(shutil.which("lpstat")),
            "registry_compat": True,
            "windows_uia": False,  # never claim Windows UIA on Linux
            "ocr": has_ocr,
            "ocr_tesseract": tesseract_available(),
            "wayland": bool(session.get("wayland")),
            "x11": bool(session.get("x11")),
            "privileged": False,  # elevation gated; never claim open privilege
            "vision": True,
        }

    def capability_flags(self) -> dict[str, bool]:
        return dict(self._caps)

    # ------------------------------------------------------------------
    # System
    # ------------------------------------------------------------------
    def get_system_info(self) -> dict[str, Any]:
        uname = platform.uname()
        return {
            "hostname": platform.node(),
            "os": "Linux",
            "os_version": f"{uname.release} ({uname.version})".strip(),
            "kernel": uname.release,
            "architecture": platform.machine(),
            "backend": self.name,
            "python": platform.python_version(),
            "user": os.environ.get("USER") or os.environ.get("LOGNAME") or "",
            "platform_real": platform.system(),
        }

    def get_resources(self) -> dict[str, Any]:
        if not self._psutil:
            return {"cpu_percent": 0, "memory": {}, "disk": {}, "note": "psutil unavailable"}
        vm = self._psutil.virtual_memory()
        disk = self._psutil.disk_usage(str(self.sandbox))
        return {
            "cpu_percent": self._psutil.cpu_percent(interval=0.05),
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

    def get_uptime(self) -> dict[str, Any]:
        if self._psutil:
            boot = self._psutil.boot_time()
            return {"uptime_seconds": time.time() - boot, "boot_time": boot}
        return {"uptime_seconds": time.time() - self._start, "boot_time": self._start}

    def power_action(self, action: str) -> dict[str, Any]:
        """Power ops are hardware/polkit protected — structured denial by default."""
        from windows_os_api.core.security.privilege import deny_structured

        return deny_structured(
            "power actions require interactive elevation / polkit and ADMIN + WINOS_ALLOW_PRIVILEGED",
            code="hardware_protected",
            detail={"action": action},
        )

    # ------------------------------------------------------------------
    # Processes (REAL)
    # ------------------------------------------------------------------
    def list_processes(self) -> list[dict[str, Any]]:
        if not self._psutil:
            return []
        out: list[dict[str, Any]] = []
        for p in self._psutil.process_iter(
            ["pid", "name", "status", "cpu_percent", "memory_info"]
        ):
            try:
                info = p.info
                mem = info.get("memory_info")
                out.append(
                    {
                        "pid": info["pid"],
                        "name": info.get("name") or "",
                        "status": info.get("status") or "",
                        "cpu_percent": info.get("cpu_percent") or 0,
                        "memory_mb": round((mem.rss / 1e6) if mem else 0, 2),
                    }
                )
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
                "running": p.is_running(),
            }
        except self._psutil.Error:
            return None

    def start_process(self, command: str, args: list[str] | None = None) -> dict[str, Any]:
        """Actually spawn a real OS process via subprocess.Popen."""
        cmd = [command, *(args or [])]
        try:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            return {"ok": False, "error": f"executable not found: {command}", "detail": str(e)}
        except OSError as e:
            return {"ok": False, "error": str(e), "command": command}
        self._children[proc.pid] = proc
        name = Path(command).name or command
        running = True
        if self._psutil:
            try:
                running = self._psutil.Process(proc.pid).is_running()
            except self._psutil.Error:
                running = proc.poll() is None
        return {
            "ok": True,
            "pid": proc.pid,
            "name": name,
            "status": "running" if running else "exited",
            "command": command,
            "args": args or [],
            "real": True,
        }

    def terminate_process(self, pid: int) -> dict[str, Any]:
        child = self._children.pop(pid, None)
        if self._psutil:
            try:
                p = self._psutil.Process(pid)
                p.terminate()
                try:
                    p.wait(timeout=3)
                except self._psutil.TimeoutExpired:
                    p.kill()
                return {"ok": True, "pid": pid, "status": "terminated", "real": True}
            except self._psutil.NoSuchProcess:
                if child is not None:
                    child.terminate()
                return {"ok": True, "pid": pid, "status": "already_gone"}
            except self._psutil.Error as e:
                return {"ok": False, "error": str(e), "pid": pid}
        if child is not None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
            return {"ok": True, "pid": pid, "status": "terminated", "real": True}
        try:
            os.kill(pid, 15)
            return {"ok": True, "pid": pid, "status": "signal_term"}
        except ProcessLookupError:
            return {"ok": False, "error": "not found", "pid": pid}
        except PermissionError as e:
            return {"ok": False, "error": str(e), "pid": pid}

    # ------------------------------------------------------------------
    # Apps
    # ------------------------------------------------------------------
    def discover_apps(self) -> list[dict[str, Any]]:
        apps: list[dict[str, Any]] = []
        desktop_dirs = [
            Path("/usr/share/applications"),
            Path.home() / ".local/share/applications",
            Path("/usr/local/share/applications"),
        ]
        seen: set[str] = set()
        for d in desktop_dirs:
            if not d.is_dir():
                continue
            for desk in sorted(d.glob("*.desktop")):
                if len(apps) >= 200:
                    break
                try:
                    text = desk.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                name = desk.stem
                exec_line = ""
                for line in text.splitlines():
                    if line.startswith("Name=") and name == desk.stem:
                        name = line.split("=", 1)[1].strip()
                    if line.startswith("Exec=") and not exec_line:
                        exec_line = line.split("=", 1)[1].strip().split()[0]
                app_id = desk.stem.lower().replace(" ", "-")
                if app_id in seen:
                    continue
                seen.add(app_id)
                apps.append(
                    {
                        "id": app_id,
                        "name": name,
                        "path": exec_line or str(desk),
                        "version": "",
                        "publisher": "",
                        "source": "desktop",
                    }
                )
        # Common binaries as fallback discovery
        for bin_name in ("bash", "python3", "jq", "curl", "git"):
            p = shutil.which(bin_name)
            if p and bin_name not in seen:
                apps.append(
                    {
                        "id": bin_name,
                        "name": bin_name,
                        "path": p,
                        "version": "",
                        "publisher": "",
                        "source": "path",
                    }
                )
        return apps

    # ------------------------------------------------------------------
    # Windows (wmctrl / xdotool — optional)
    # ------------------------------------------------------------------
    def list_windows(self) -> list[dict[str, Any]]:
        if shutil.which("wmctrl"):
            try:
                r = subprocess.run(  # noqa: S603
                    ["wmctrl", "-l"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=_display_env(),
                )
                out: list[dict[str, Any]] = []
                for line in r.stdout.splitlines():
                    parts = line.split(None, 3)
                    if len(parts) < 4:
                        continue
                    hwnd_s, _desk, _host, title = parts
                    try:
                        hwnd = int(hwnd_s, 16)
                    except ValueError:
                        continue
                    out.append({"hwnd": hwnd, "title": title, "visible": True})
                return out
            except Exception:  # noqa: BLE001
                pass
        if shutil.which("xdotool"):
            try:
                r = subprocess.run(  # noqa: S603
                    ["xdotool", "search", "--name", ".*"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=_display_env(),
                )
                out = []
                for line in r.stdout.splitlines():
                    line = line.strip()
                    if not line.isdigit():
                        continue
                    hwnd = int(line)
                    title_r = subprocess.run(  # noqa: S603
                        ["xdotool", "getwindowname", str(hwnd)],
                        capture_output=True,
                        text=True,
                        timeout=2,
                        env=_display_env(),
                    )
                    title = title_r.stdout.strip()
                    if title:
                        out.append({"hwnd": hwnd, "title": title, "visible": True})
                return out
            except Exception:  # noqa: BLE001
                return []
        # Wayland best-effort window list
        session = detect_session()
        if session.get("wayland"):
            tool = wayland_window_tool()
            cmd = build_wayland_list_windows_cmd(tool)
            if cmd and tool not in (None, "wayland-info"):
                try:
                    r = subprocess.run(  # noqa: S603
                        cmd, capture_output=True, text=True, timeout=5
                    )
                    out: list[dict[str, Any]] = []
                    if tool == "hyprctl":
                        try:
                            data = json.loads(r.stdout or "[]")
                            for i, c in enumerate(data if isinstance(data, list) else []):
                                addr = c.get("address")
                                if isinstance(addr, str) and addr.startswith("0x"):
                                    try:
                                        hwnd = int(addr, 16)
                                    except ValueError:
                                        hwnd = i + 1
                                else:
                                    hwnd = i + 1
                                out.append(
                                    {
                                        "hwnd": hwnd,
                                        "title": c.get("title") or c.get("class") or "",
                                        "visible": True,
                                        "compositor": "hyprland",
                                    }
                                )
                        except json.JSONDecodeError:
                            pass
                    else:
                        for i, line in enumerate((r.stdout or "").splitlines()):
                            line = line.strip()
                            if not line:
                                continue
                            out.append(
                                {
                                    "hwnd": i + 1,
                                    "title": line[:200],
                                    "visible": True,
                                    "compositor": tool,
                                }
                            )
                    if out:
                        return out
                except Exception:  # noqa: BLE001
                    pass
            # Honest empty with note when compositor unknown / tools missing
            return []
        return []

    def get_window(self, hwnd: int) -> dict[str, Any] | None:
        wins = {w["hwnd"]: w for w in self.list_windows()}
        return wins.get(hwnd)

    def active_window(self) -> int | None:
        """Which window currently holds the focus, or None if that cannot be read."""
        ok, out = self._xdotool(["getactivewindow"], timeout=5)
        if not ok:
            return None
        try:
            return int((out or "").strip())
        except ValueError:
            return None

    def focus_window(self, hwnd: int) -> dict[str, Any]:
        """Give a window the focus, and CHECK that it got it.

        The previous version ran the tool with `check=False` and returned
        `{"ok": True}` whatever happened — including for a window that did not
        exist. Focus is readable (`xdotool getactivewindow`), so there is no
        reason to assume it: `ok` here means the window holds the focus now, on
        the same terms as the geometry operations in #18.
        """
        if not shutil.which("xdotool") and not shutil.which("wmctrl"):
            return failure(TOOL_UNAVAILABLE, "wmctrl/xdotool not installed",
                           hwnd=hwnd, windows_ui=False)
        if self.window_geometry(hwnd) is None:
            return failure(WINDOW_NOT_FOUND, f"window {hwnd} not found", hwnd=hwnd)

        ran, detail = self._xdotool(["windowactivate", "--sync", str(hwnd)], timeout=10)
        if not ran and shutil.which("wmctrl"):
            self._x_tool(["wmctrl", "-i", "-a", str(hwnd)])

        active = self.active_window()
        if active is None:
            # "Not verified" is not the same as failed — and it is certainly not
            # success, which is what this used to report.
            return failure(
                FOCUS_NOT_GRANTED,
                "could not read which window holds the focus"
                + (f": {detail}" if detail else ""),
                hwnd=hwnd, verified=False,
            )
        if active != hwnd:
            # A window manager may refuse focus — focus-stealing prevention is a
            # feature, not a fault. But reporting success would leave a caller
            # typing into whatever window actually has it.
            return failure(
                FOCUS_NOT_GRANTED,
                f"window {active} holds the focus, not {hwnd}",
                hwnd=hwnd, active_window=active, verified=True,
            )
        return {"ok": True, "hwnd": hwnd, "active_window": active, "verified": True}

    def close_window(self, hwnd: int, timeout: float = 5.0) -> dict[str, Any]:
        """Ask a window to close, and WAIT to see whether it did.

        Closing is a request, not a command: `wmctrl -i -c` and
        `xdotool windowclose` both send WM_DELETE_WINDOW, and an application
        with an unsaved document is entitled to put up "save changes?" and stay
        open. So this polls until the window is actually gone and reports
        WINDOW_STILL_OPEN when it is not.

        The exit code cannot stand in for that. Measured against a window whose
        process had already been killed: `xdotool windowclose` exits 1 and
        `wmctrl -i -c` exits **0** — the same lie as `wmctrl -b
        add,maximized_vert` in #18.

        Note it closes a WINDOW, not an application: a program with other
        windows open goes on running.
        """
        if not shutil.which("xdotool") and not shutil.which("wmctrl"):
            return failure(TOOL_UNAVAILABLE, "wmctrl/xdotool not installed",
                           hwnd=hwnd, windows_ui=False)
        if self.window_geometry(hwnd) is None:
            return failure(WINDOW_NOT_FOUND, f"window {hwnd} not found", hwnd=hwnd)

        ran, detail = self._xdotool(["windowclose", str(hwnd)], timeout=5)
        if not ran and shutil.which("wmctrl"):
            self._x_tool(["wmctrl", "-i", "-c", str(hwnd)])

        deadline = time.time() + max(timeout, 0.1)
        while time.time() < deadline:
            if self.window_geometry(hwnd) is None:
                return {"ok": True, "hwnd": hwnd, "closed": True, "verified": True}
            time.sleep(0.05)

        return failure(
            WINDOW_STILL_OPEN,
            f"window {hwnd} was asked to close and is still open after "
            f"{timeout:g}s — an unsaved document or a confirmation dialog will "
            "do this" + (f" ({detail})" if detail else ""),
            hwnd=hwnd, closed=False, verified=True,
        )

    # ------------------------------------------------------------------
    # Window geometry / state
    #
    # Every operation here reads the result back from the X server instead of
    # trusting the tool's exit code. That is not defensive habit, it is a
    # measured necessity: `wmctrl -i -r 99999999 -b add,maximized_vert` exits
    # **0** for a window id that does not exist. A method returning
    # `{"ok": True}` on the strength of that exit code would report success for
    # an operation that never touched anything.
    # ------------------------------------------------------------------
    def _x_tool(self, args: list[str], timeout: int = 5) -> Any:
        """Run an X tool, or return None if the binary is absent / the call blew up."""
        if not shutil.which(args[0]):
            return None
        try:
            return subprocess.run(  # noqa: S603
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=_display_env(),
            )
        except Exception:  # noqa: BLE001
            return None

    def window_geometry(self, hwnd: int) -> dict[str, int] | None:
        """Geometry as the X server reports it, or None when the window is gone."""
        r = self._x_tool(["xdotool", "getwindowgeometry", "--shell", str(hwnd)])
        if r is None or r.returncode != 0:
            return None
        vals: dict[str, int] = {}
        for line in (r.stdout or "").splitlines():
            key, _, raw = line.partition("=")
            key = key.strip().lower()
            if key not in ("x", "y", "width", "height"):
                continue
            try:
                vals[key] = int(raw.strip())
            except ValueError:
                continue
        return vals if len(vals) == 4 else None

    def _wm_state(self, hwnd: int) -> list[str] | None:
        """`_NET_WM_STATE` atoms, or None when they cannot be read.

        None means "not verified" and is reported as such. It is not quietly
        turned into "normal", which would be an invented answer.
        """
        r = self._x_tool(["xprop", "-id", str(hwnd), "_NET_WM_STATE"])
        if r is None or r.returncode != 0:
            return None
        out = (r.stdout or "").strip()
        if "_NET_WM_STATE" not in out:
            return None
        _, _, rhs = out.partition("=")
        return [atom.strip() for atom in rhs.split(",") if atom.strip()]

    @staticmethod
    def _classify_state(atoms: list[str]) -> str:
        joined = " ".join(atoms)
        if "_NET_WM_STATE_HIDDEN" in joined:
            return "minimized"
        if "_NET_WM_STATE_MAXIMIZED_VERT" in joined and "_NET_WM_STATE_MAXIMIZED_HORZ" in joined:
            return "maximized"
        return "normal"

    def _geometry_op(
        self, hwnd: int, args: list[str], requested: dict[str, int]
    ) -> dict[str, Any]:
        """Shared body of move/resize: act, then report what actually happened."""
        if not shutil.which("xdotool"):
            return {"ok": False, "error": "xdotool not installed", "hwnd": hwnd,
                    "windows_ui": False}
        before = self.window_geometry(hwnd)
        if before is None:
            return {"ok": False, "error": f"window {hwnd} not found", "hwnd": hwnd}
        r = self._x_tool(args)
        if r is None or r.returncode != 0:
            detail = (r.stderr or "").strip() if r is not None else "xdotool call failed"
            return {"ok": False, "error": detail or "xdotool reported failure",
                    "hwnd": hwnd, "geometry": before}
        after = self.window_geometry(hwnd)
        if after is None:
            return {"ok": False, "error": "window disappeared during the operation",
                    "hwnd": hwnd}
        return {
            "ok": True,
            "hwnd": hwnd,
            "requested": requested,
            "geometry": after,
            "previous": before,
        }

    def move_window(self, hwnd: int, x: int, y: int) -> dict[str, Any]:
        try:
            x, y = validate_position(x, y)
        except GeometryRejected as exc:
            return {"ok": False, "error": str(exc), "hwnd": hwnd}
        return self._geometry_op(
            hwnd, ["xdotool", "windowmove", str(hwnd), str(x), str(y)], {"x": x, "y": y}
        )

    def resize_window(self, hwnd: int, width: int, height: int) -> dict[str, Any]:
        try:
            width, height = validate_size(width, height)
        except GeometryRejected as exc:
            return {"ok": False, "error": str(exc), "hwnd": hwnd}
        return self._geometry_op(
            hwnd,
            ["xdotool", "windowsize", str(hwnd), str(width), str(height)],
            {"width": width, "height": height},
        )

    def _state_op(self, hwnd: int, args: list[str], expected: str) -> dict[str, Any]:
        """Shared body of minimize/maximize.

        `verified` is the honest part: `xprop` (x11-utils) is what confirms the
        window manager honoured the request. Without it the tool call may well
        have worked, but this layer did not see it happen, and says so rather
        than asserting an outcome it cannot back.
        """
        if not shutil.which(args[0]):
            return {"ok": False, "error": f"{args[0]} not installed", "hwnd": hwnd,
                    "windows_ui": False}
        if self.window_geometry(hwnd) is None:
            return {"ok": False, "error": f"window {hwnd} not found", "hwnd": hwnd}
        r = self._x_tool(args)
        if r is None or r.returncode != 0:
            detail = (r.stderr or "").strip() if r is not None else f"{args[0]} call failed"
            return {"ok": False, "error": detail or f"{args[0]} reported failure", "hwnd": hwnd}
        return self._report_state(hwnd, expected)

    def _report_state(self, hwnd: int, expected: str) -> dict[str, Any]:
        atoms = self._wm_state(hwnd)
        if atoms is None:
            return {
                "ok": True,
                "hwnd": hwnd,
                "state": expected,
                "verified": False,
                "note": "install x11-utils (xprop) to confirm the window manager honoured this",
                "geometry": self.window_geometry(hwnd),
            }
        observed = self._classify_state(atoms)
        result: dict[str, Any] = {
            "ok": observed == expected,
            "hwnd": hwnd,
            "state": observed,
            "requested_state": expected,
            "verified": True,
            "wm_state": atoms,
            "geometry": self.window_geometry(hwnd),
        }
        if observed != expected:
            result["error"] = (
                f"window manager left the window {observed!r}, not {expected!r}"
            )
        return result

    def minimize_window(self, hwnd: int) -> dict[str, Any]:
        return self._state_op(hwnd, ["xdotool", "windowminimize", str(hwnd)], "minimized")

    def maximize_window(self, hwnd: int) -> dict[str, Any]:
        return self._state_op(
            hwnd,
            ["wmctrl", "-i", "-r", str(hwnd), "-b", "add,maximized_vert,maximized_horz"],
            "maximized",
        )

    def restore_window(self, hwnd: int) -> dict[str, Any]:
        """Clear both maximize axes and map the window back.

        Two tools, because neither does both: wmctrl drops the maximize atoms,
        xdotool maps a minimized window back. Running only the first on a
        minimized window would report "normal" while it stayed invisible.
        """
        if not shutil.which("xdotool") and not shutil.which("wmctrl"):
            return {"ok": False, "error": "wmctrl/xdotool not installed", "hwnd": hwnd,
                    "windows_ui": False}
        if self.window_geometry(hwnd) is None:
            return {"ok": False, "error": f"window {hwnd} not found", "hwnd": hwnd}
        self._x_tool(
            ["wmctrl", "-i", "-r", str(hwnd), "-b", "remove,maximized_vert,maximized_horz"]
        )
        self._x_tool(["xdotool", "windowactivate", str(hwnd)])
        return self._report_state(hwnd, "normal")

    # ------------------------------------------------------------------
    # UI tree (AT-SPI optional — never Fake Contoso on linux)
    # ------------------------------------------------------------------
    def _atspi_states(self, acc: Any, pyatspi: Any) -> list[str]:
        out: list[str] = []
        try:
            st = acc.getState()
        except Exception:  # noqa: BLE001
            return out
        for attr in dir(pyatspi):
            if not attr.startswith("STATE_"):
                continue
            try:
                val = getattr(pyatspi, attr)
                if st.contains(val):
                    out.append(attr.replace("STATE_", "").lower())
            except Exception:  # noqa: BLE001
                continue
        return out

    def _atspi_bounds(self, acc: Any) -> dict[str, int] | None:
        try:
            comp = acc.queryComponent()
            ext = comp.getExtents(0)
            return {"x": int(ext.x), "y": int(ext.y), "width": int(ext.width), "height": int(ext.height)}
        except Exception:  # noqa: BLE001
            return None

    def _atspi_path(self, path_parts: list[str]) -> str:
        return "/" + "/".join(path_parts) if path_parts else "/"

    def _atspi_identity(
        self, acc: Any, path_parts: list[str]
    ) -> tuple[str, str, str, str]:
        try:
            name = acc.name or ""
        except Exception:  # noqa: BLE001
            name = ""
        try:
            role = acc.getRoleName() if hasattr(acc, "getRoleName") else ""
        except Exception:  # noqa: BLE001
            role = ""
        part = f"{role}:{name}" if name else role or f"node{len(path_parts)}"
        return name, role, part, self._atspi_path(path_parts + [part])

    def _atspi_node(
        self,
        acc: Any,
        pyatspi: Any,
        depth: int = 0,
        max_depth: int = 6,
        path_parts: list[str] | None = None,
    ) -> dict[str, Any]:
        path_parts = list(path_parts or [])
        name, role, part, automation_id = self._atspi_identity(acc, path_parts)
        cur_path = path_parts + [part]
        states = self._atspi_states(acc, pyatspi)
        bounds = self._atspi_bounds(acc)
        value = None
        try:
            text_iface = acc.queryText()
            value = text_iface.getText(0, min(text_iface.characterCount, 500))
        except Exception:  # noqa: BLE001
            pass
        kids: list[dict[str, Any]] = []
        if depth < max_depth:
            try:
                n = acc.childCount
            except Exception:  # noqa: BLE001
                n = 0
            for i in range(min(n, 60)):
                try:
                    kids.append(
                        self._atspi_node(
                            acc.getChildAtIndex(i),
                            pyatspi,
                            depth + 1,
                            max_depth,
                            cur_path,
                        )
                    )
                except Exception:  # noqa: BLE001
                    continue
        return {
            "name": name,
            "role": role,
            "control_type": role,
            "states": states,
            "bounds": bounds,
            "automation_id": automation_id,
            "path": automation_id,
            "value": value,
            "children": kids,
        }

    def _find_atspi_acc_by_path(self, pyatspi: Any, automation_id: str) -> Any | None:
        desktop = pyatspi.Registry.getDesktop(0)

        def walk(acc: Any, path_parts: list[str]) -> Any | None:
            _name, _role, part, current_id = self._atspi_identity(acc, path_parts)
            if current_id == automation_id:
                return acc
            current_parts = path_parts + [part]
            try:
                count = acc.childCount
            except Exception:  # noqa: BLE001
                count = 0
            for index in range(min(count, 80)):
                try:
                    found = walk(acc.getChildAtIndex(index), current_parts)
                except Exception:  # noqa: BLE001
                    continue
                if found is not None:
                    return found
            return None

        try:
            for index in range(min(desktop.childCount, 40)):
                found = walk(desktop.getChildAtIndex(index), [])
                if found is not None:
                    return found
        except Exception:  # noqa: BLE001
            return None
        return None

    def set_ui_value(self, automation_id: str, value: str) -> dict[str, Any]:
        """Set one exact AT-SPI node and report only a real EditableText write."""
        try:
            pyatspi = _ensure_pyatspi()
            acc = self._find_atspi_acc_by_path(pyatspi, automation_id)
            if acc is None:
                return {
                    "ok": False,
                    "error": "AT-SPI element not found",
                    "automation_id": automation_id,
                }
            editable = acc.queryEditableText()
            try:
                text = acc.queryText()
                if text.characterCount > 0:
                    editable.deleteText(0, text.characterCount)
            except Exception:  # noqa: BLE001
                pass
            editable.insertText(0, value, len(value))
            return {
                "ok": True,
                "method": "atspi_editable_text",
                "automation_id": automation_id,
                "length": len(value),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": str(exc),
                "automation_id": automation_id,
            }

    def get_ui_tree(self, hwnd: int | None = None) -> dict[str, Any]:
        try:
            pyatspi = _ensure_pyatspi()
            desktop = pyatspi.Registry.getDesktop(0)
            children: list[dict[str, Any]] = []
            try:
                count = desktop.childCount
            except Exception:  # noqa: BLE001
                count = 0
            for i in range(min(count, 40)):
                try:
                    children.append(self._atspi_node(desktop.getChildAtIndex(i), pyatspi))
                except Exception:  # noqa: BLE001
                    continue
            return {
                "hwnd": hwnd,
                "name": "AT-SPI desktop",
                "control_type": "Desktop",
                "role": "desktop frame",
                "states": [],
                "bounds": None,
                "automation_id": "/desktop",
                "path": "/desktop",
                "children": children,
                "backend": "atspi",
                "supported": True,
            }
        except Exception as e:  # noqa: BLE001
            return {
                "hwnd": hwnd,
                "name": "",
                "control_type": "Unsupported",
                "children": [],
                "supported": False,
                "error": "AT-SPI / pyatspi unavailable",
                "detail": str(e),
                "hint": "Install python3-pyatspi; ensure at-spi2-core is running. "
                "Set WINOS_BACKEND=fake for Contoso CRM adapter demos",
            }

    def find_accessible(
        self,
        name: str | None = None,
        role: str | None = None,
        *,
        exact: bool = False,
    ) -> dict[str, Any] | None:
        """Find first AT-SPI node matching name and/or role."""
        tree = self.get_ui_tree()
        if not tree.get("supported", True) and tree.get("error"):
            return None

        def match(node: dict[str, Any]) -> bool:
            n = node.get("name") or ""
            r = (node.get("role") or node.get("control_type") or "").lower()
            ok_name = True
            ok_role = True
            if name is not None:
                if exact:
                    ok_name = n == name
                else:
                    ok_name = name.lower() in n.lower()
            if role is not None:
                ok_role = role.lower() in r
            return ok_name and ok_role

        stack = list(tree.get("children") or [])
        while stack:
            node = stack.pop(0)
            if match(node):
                return node
            stack[0:0] = list(node.get("children") or [])
        return None

    def _find_atspi_acc(
        self,
        pyatspi: Any,
        name: str | None = None,
        role: str | None = None,
        *,
        exact: bool = False,
    ) -> Any | None:
        desktop = pyatspi.Registry.getDesktop(0)

        def walk(acc: Any) -> Any | None:
            try:
                n = acc.name or ""
            except Exception:  # noqa: BLE001
                n = ""
            try:
                r = acc.getRoleName() if hasattr(acc, "getRoleName") else ""
            except Exception:  # noqa: BLE001
                r = ""
            ok_name = True
            ok_role = True
            if name is not None:
                ok_name = (n == name) if exact else (name.lower() in n.lower())
            if role is not None:
                ok_role = role.lower() in (r or "").lower()
            if ok_name and ok_role and (name is not None or role is not None):
                return acc
            try:
                count = acc.childCount
            except Exception:  # noqa: BLE001
                count = 0
            for i in range(min(count, 80)):
                try:
                    found = walk(acc.getChildAtIndex(i))
                except Exception:  # noqa: BLE001
                    continue
                if found is not None:
                    return found
            return None

        try:
            for i in range(min(desktop.childCount, 40)):
                found = walk(desktop.getChildAtIndex(i))
                if found is not None:
                    return found
        except Exception:  # noqa: BLE001
            return None
        return None

    def accessible_click(self, name: str, role: str | None = None) -> dict[str, Any]:
        """Click an accessible via AT-SPI Action, else Component.grabFocus + click bounds."""
        try:
            pyatspi = _ensure_pyatspi()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"pyatspi unavailable: {e}", "name": name}

        acc = self._find_atspi_acc(pyatspi, name=name, role=role)
        if acc is None:
            return {"ok": False, "error": "accessible not found", "name": name, "role": role}

        # Prefer Action interface
        try:
            action = acc.queryAction()
            n_act = action.nActions
            click_idx = None
            for i in range(n_act):
                an = (action.getName(i) or "").lower()
                if an in ("click", "press", "activate", "jump"):
                    click_idx = i
                    break
            if click_idx is None and n_act > 0:
                click_idx = 0
            if click_idx is not None:
                action.doAction(click_idx)
                return {
                    "ok": True,
                    "name": name,
                    "role": role,
                    "method": "atspi_action",
                    "action_index": click_idx,
                }
        except Exception:  # noqa: BLE001
            pass

        # Fallback: focus + mouse click at center of bounds
        bounds = self._atspi_bounds(acc)
        try:
            acc.queryComponent().grabFocus()
        except Exception:  # noqa: BLE001
            pass
        if bounds and bounds.get("width", 0) > 0 and bounds.get("height", 0) > 0:
            x = bounds["x"] + bounds["width"] // 2
            y = bounds["y"] + bounds["height"] // 2
            clicked = self.mouse_click(x, y, "left")
            return {
                "ok": bool(clicked.get("ok")),
                "name": name,
                "role": role,
                "method": "bounds_click",
                "bounds": bounds,
                "click": clicked,
            }
        return {"ok": False, "error": "no Action interface and no usable bounds", "name": name}

    def accessible_set_text(self, name: str, text: str, role: str | None = None) -> dict[str, Any]:
        """Set text via AT-SPI EditableText / Text when available."""
        try:
            pyatspi = _ensure_pyatspi()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"pyatspi unavailable: {e}", "name": name}

        # Prefer matching the named node; if name looks like window title, find text child under it
        acc = self._find_atspi_acc(pyatspi, name=name, role=role)
        if acc is None:
            return {"ok": False, "error": "accessible not found", "name": name, "role": role}

        def try_set(target: Any) -> dict[str, Any] | None:
            try:
                editable = target.queryEditableText()
                try:
                    # clear existing
                    t = target.queryText()
                    n = t.characterCount
                    if n > 0:
                        editable.deleteText(0, n)
                except Exception:  # noqa: BLE001
                    pass
                editable.insertText(0, text, len(text))
                return {"ok": True, "name": name, "method": "editable_text", "length": len(text)}
            except Exception:  # noqa: BLE001
                pass
            try:
                t = target.queryText()
                # Some widgets only expose Text; still report current value path
                _ = t.characterCount
            except Exception:  # noqa: BLE001
                return None
            return None

        direct = try_set(acc)
        if direct:
            return direct

        # Search descendants for editable text
        stack = [acc]
        while stack:
            cur = stack.pop(0)
            got = try_set(cur)
            if got:
                return got
            try:
                for i in range(min(cur.childCount, 80)):
                    stack.append(cur.getChildAtIndex(i))
            except Exception:  # noqa: BLE001
                continue

        # Last resort: focus + xdotool type
        try:
            acc.queryComponent().grabFocus()
        except Exception:  # noqa: BLE001
            pass
        typed = self.type_text(text)
        return {
            "ok": bool(typed.get("ok")),
            "name": name,
            "method": "xdotool_fallback",
            "type_result": typed,
        }

    # ------------------------------------------------------------------
    # Input (X11 xdotool / Wayland ydotool|wtype|dotool)
    # ------------------------------------------------------------------
    def mouse_move(self, x: int, y: int) -> dict[str, Any]:
        session = detect_session()
        if session.get("x11") and shutil.which("xdotool"):
            try:
                x, y = validate_position(x, y)
            except GeometryRejected as exc:
                return {"ok": False, "error": str(exc), "x": x, "y": y}
            ok, detail = self._xdotool(["mousemove", str(x), str(y)], timeout=5)
            if not ok:
                return {"ok": False, "error": detail, "x": x, "y": y}
            # The pointer is one of the few input effects X reports back, so
            # this checks instead of assuming — and the check is not academic:
            # on a bare Xvfb with NO window manager, `xdotool mousemove` is a
            # silent no-op and the pointer stays at the screen centre. This used
            # to return {"ok": True} for a move that never happened, and the
            # methods below branch on that answer.
            position = self.pointer_position()
            if position is None:
                return {"ok": False, "error": "could not read the pointer position",
                        "requested": {"x": x, "y": y}}
            if position != {"x": x, "y": y}:
                return {
                    "ok": False,
                    "error": (f"pointer stayed at {position} instead of moving to "
                              f"{{'x': {x}, 'y': {y}}} — is a window manager running?"),
                    "requested": {"x": x, "y": y},
                    "position": position,
                    "backend": "xdotool",
                }
            return {"ok": True, "x": x, "y": y, "position": position, "backend": "xdotool"}
        if session.get("wayland"):
            tool = wayland_input_tool()
            cmd = build_wayland_click_cmd(x, y, tool=tool)
            # ydotool mousemove+click bundled; for move-only try ydotool mousemove
            if tool == "ydotool" and shutil.which("ydotool"):
                try:
                    subprocess.run(  # noqa: S603
                        ["ydotool", "mousemove", "--absolute", str(x), str(y)],
                        capture_output=True,
                        timeout=5,
                        check=False,
                    )
                    return {"ok": True, "x": x, "y": y, "backend": "ydotool"}
                except Exception as e:  # noqa: BLE001
                    return {"ok": False, "error": str(e), "backend": "ydotool"}
            return {
                "ok": False,
                "error": "Wayland mouse move requires ydotool (wtype cannot move pointer)",
                "wayland": True,
                "tool": tool,
            }
        return {"ok": False, "error": "xdotool not installed and not on Wayland with ydotool"}

    def mouse_click(self, x: int, y: int, button: str = "left") -> dict[str, Any]:
        session = detect_session()
        # BREAKING: an unknown button used to fall through to left — a typo
        # produced a left click reported as the button the caller named. Refused
        # now, because reporting an action you did not take is worse than
        # refusing one you cannot.
        try:
            button = validate_button(button)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "x": x, "y": y, "button": button}
        if session.get("x11") and shutil.which("xdotool"):
            self.mouse_move(x, y)
            btn = str(MOUSE_BUTTONS[button])
            try:
                subprocess.run(  # noqa: S603
                    ["xdotool", "click", btn],
                    capture_output=True,
                    timeout=5,
                    check=False,
                    env=_display_env(),
                )
                return {"ok": True, "x": x, "y": y, "button": button, "backend": "xdotool"}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}
        if session.get("wayland"):
            tool = wayland_input_tool()
            cmd = build_wayland_click_cmd(x, y, button=button, tool=tool)
            if cmd and tool == "ydotool":
                try:
                    # split: move then click for reliability
                    subprocess.run(  # noqa: S603
                        ["ydotool", "mousemove", "--absolute", str(x), str(y)],
                        capture_output=True,
                        timeout=5,
                        check=False,
                    )
                    btn = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}.get(button, "0xC0")
                    subprocess.run(  # noqa: S603
                        ["ydotool", "click", btn],
                        capture_output=True,
                        timeout=5,
                        check=False,
                    )
                    return {"ok": True, "x": x, "y": y, "button": button, "backend": "ydotool"}
                except Exception as e:  # noqa: BLE001
                    return {"ok": False, "error": str(e), "backend": "ydotool"}
            return {
                "ok": False,
                "error": "Wayland click requires ydotool; portal input not implemented",
                "wayland": True,
                "tool": tool,
                "x": x,
                "y": y,
                "button": button,
            }
        return {"ok": False, "error": "xdotool not installed", "x": x, "y": y, "button": button}

    def key_press(self, key: str, modifiers: list[str] | None = None) -> dict[str, Any]:
        session = detect_session()
        if session.get("x11") and shutil.which("xdotool"):
            seq = "+".join([*(modifiers or []), key])
            try:
                subprocess.run(  # noqa: S603
                    ["xdotool", "key", seq],
                    capture_output=True,
                    timeout=5,
                    check=False,
                    env=_display_env(),
                )
                return {"ok": True, "key": key, "modifiers": modifiers or [], "backend": "xdotool"}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}
        if session.get("wayland"):
            tool = wayland_input_tool()
            cmd = build_wayland_key_cmd(key, modifiers, tool=tool)
            if cmd:
                try:
                    subprocess.run(cmd, capture_output=True, timeout=5, check=False)  # noqa: S603
                    return {
                        "ok": True,
                        "key": key,
                        "modifiers": modifiers or [],
                        "backend": tool,
                    }
                except Exception as e:  # noqa: BLE001
                    return {"ok": False, "error": str(e), "backend": tool}
            return {
                "ok": False,
                "error": "Wayland key requires ydotool/wtype",
                "wayland": True,
                "key": key,
            }
        return {"ok": False, "error": "xdotool not installed", "key": key}

    def type_text(self, text: str) -> dict[str, Any]:
        session = detect_session()
        if session.get("x11") and shutil.which("xdotool"):
            try:
                subprocess.run(  # noqa: S603
                    ["xdotool", "type", "--clearmodifiers", "--", text],
                    capture_output=True,
                    timeout=30,
                    check=False,
                    env=_display_env(),
                )
                return {"ok": True, "length": len(text), "backend": "xdotool"}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e)}
        if session.get("wayland"):
            tool = wayland_input_tool()
            cmd = build_wayland_type_cmd(text, tool=tool)
            if cmd and tool in ("ydotool", "wtype"):
                try:
                    subprocess.run(cmd, capture_output=True, timeout=30, check=False)  # noqa: S603
                    return {"ok": True, "length": len(text), "backend": tool}
                except Exception as e:  # noqa: BLE001
                    return {"ok": False, "error": str(e), "backend": tool}
            return {
                "ok": False,
                "error": "Wayland type requires ydotool or wtype",
                "wayland": True,
                "tool": tool,
            }
        return {"ok": False, "error": "xdotool not installed"}

    # ------------------------------------------------------------------
    # Input: the rest of the primitives
    #
    # What `ok` means here, and what it does not
    # ------------------------------------------
    # Window geometry could be read back from the X server, so `ok` there means
    # "the effect was observed". Keystrokes and clicks have no such readback:
    # once the event is handed to the X server it belongs to whatever window has
    # focus, and nothing reports whether that window did anything with it.
    #
    # So `ok` here means the narrower, true thing — **the X server accepted the
    # event** — which is still strictly more than the surrounding code claimed,
    # because that ignored the exit code and returned `ok: True` regardless.
    # Delivery itself is proven where it can be: the tests run `xev`, which
    # prints every event the X server actually delivers, and assert on that.
    #
    # The pointer IS readable, so move and drag verify their final position.
    # ------------------------------------------------------------------
    def _xdotool(self, args: list[str], timeout: int = 10) -> tuple[bool, str]:
        """Run xdotool and REPORT its exit code. Returns (ok, detail)."""
        if not shutil.which("xdotool"):
            return False, "xdotool not installed"
        try:
            r = subprocess.run(  # noqa: S603
                ["xdotool", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=_display_env(),
            )
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        if r.returncode != 0:
            return False, (r.stderr or "").strip() or f"xdotool exited {r.returncode}"
        return True, (r.stdout or "").strip()

    def pointer_position(self) -> dict[str, int] | None:
        """Where the pointer actually is, or None when it cannot be read."""
        ok, out = self._xdotool(["getmouselocation", "--shell"], timeout=5)
        if not ok:
            return None
        vals: dict[str, int] = {}
        for line in out.splitlines():
            key, _, raw = line.partition("=")
            if key.strip().lower() in ("x", "y"):
                try:
                    vals[key.strip().lower()] = int(raw.strip())
                except ValueError:
                    continue
        return vals if len(vals) == 2 else None

    def _x11_ready(self) -> dict[str, Any] | None:
        """The refusal to return when this host cannot do X11 input at all."""
        session = detect_session()
        if session.get("x11") and shutil.which("xdotool"):
            return None
        if session.get("wayland"):
            return {
                "ok": False,
                "error": "not implemented on Wayland; requires ydotool with a portal session",
                "wayland": True,
                "tool": wayland_input_tool(),
            }
        return {"ok": False, "error": "xdotool not installed"}

    def double_click(self, x: int, y: int, button: str = "left") -> dict[str, Any]:
        try:
            button = validate_button(button)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "x": x, "y": y, "button": button}
        refusal = self._x11_ready()
        if refusal:
            return refusal
        moved = self.mouse_move(x, y)
        if not moved.get("ok"):
            return {**moved, "ok": False}
        # One `click --repeat 2` rather than two calls: the gap between two
        # separate processes can exceed the double-click interval, and then the
        # application sees two single clicks, which is a different gesture.
        ok, detail = self._xdotool(
            ["click", "--repeat", "2", "--delay", "80", str(MOUSE_BUTTONS[button])]
        )
        if not ok:
            return {"ok": False, "error": detail, "x": x, "y": y, "button": button}
        return {"ok": True, "x": x, "y": y, "button": button, "clicks": 2,
                "backend": "xdotool"}

    def scroll(self, direction: str = "down", amount: int = 3,
               x: int | None = None, y: int | None = None) -> dict[str, Any]:
        try:
            direction, amount = validate_scroll(direction, amount)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "direction": direction, "amount": amount}
        refusal = self._x11_ready()
        if refusal:
            return refusal
        if x is not None and y is not None:
            moved = self.mouse_move(x, y)
            if not moved.get("ok"):
                return {**moved, "ok": False}
        # On X11 a wheel notch IS a button press: 4/5 vertical, 6/7 horizontal.
        ok, detail = self._xdotool(
            ["click", "--repeat", str(amount), str(SCROLL_BUTTONS[direction])]
        )
        if not ok:
            return {"ok": False, "error": detail, "direction": direction, "amount": amount}
        return {"ok": True, "direction": direction, "amount": amount,
                "button": SCROLL_BUTTONS[direction], "backend": "xdotool"}

    def key_down(self, key: str) -> dict[str, Any]:
        """Press and HOLD. The matching key_up is the caller's responsibility."""
        try:
            key = validate_key(key)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "key": key}
        refusal = self._x11_ready()
        if refusal:
            return refusal
        ok, detail = self._xdotool(["keydown", key])
        if not ok:
            return {"ok": False, "error": detail, "key": key}
        return {"ok": True, "key": key, "state": "down", "backend": "xdotool"}

    def key_up(self, key: str) -> dict[str, Any]:
        try:
            key = validate_key(key)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "key": key}
        refusal = self._x11_ready()
        if refusal:
            return refusal
        ok, detail = self._xdotool(["keyup", key])
        if not ok:
            return {"ok": False, "error": detail, "key": key}
        return {"ok": True, "key": key, "state": "up", "backend": "xdotool"}

    def hotkey(self, keys: list[str]) -> dict[str, Any]:
        """A chord — all keys down together, then released."""
        try:
            keys = validate_hotkey(keys)
        except InputRejected as exc:
            return {"ok": False, "error": str(exc), "keys": keys}
        refusal = self._x11_ready()
        if refusal:
            return refusal
        chord = "+".join(keys)
        ok, detail = self._xdotool(["key", "--clearmodifiers", chord])
        if not ok:
            return {"ok": False, "error": detail, "keys": keys, "chord": chord}
        return {"ok": True, "keys": keys, "chord": chord, "backend": "xdotool"}

    def mouse_drag(self, x1: int, y1: int, x2: int, y2: int,
                   button: str = "left", *, steps: int = 10) -> dict[str, Any]:
        """Press at (x1,y1), move to (x2,y2), release. Parity with WindowsBackend.

        The pointer is one of the few input effects X will report back, so this
        checks where it ended up instead of assuming the moves landed.
        """
        try:
            button = validate_button(button)
            steps = validate_steps(steps)
            x1, y1 = validate_position(x1, y1)
            x2, y2 = validate_position(x2, y2)
        except (InputRejected, GeometryRejected) as exc:
            return {"ok": False, "error": str(exc), "button": button}
        refusal = self._x11_ready()
        if refusal:
            return refusal

        btn = str(MOUSE_BUTTONS[button])
        moved = self.mouse_move(x1, y1)
        if not moved.get("ok"):
            return {**moved, "ok": False}
        ok, detail = self._xdotool(["mousedown", btn])
        if not ok:
            return {"ok": False, "error": detail, "phase": "mousedown"}
        try:
            for i in range(1, steps + 1):
                t = i / steps
                xi = int(x1 + (x2 - x1) * t)
                yi = int(y1 + (y2 - y1) * t)
                step_ok, step_detail = self._xdotool(["mousemove", str(xi), str(yi)], timeout=5)
                if not step_ok:
                    return {"ok": False, "error": step_detail, "phase": "mousemove"}
        finally:
            # Release even if a move failed. A button left held down is a mouse
            # the user cannot use — the failure must not also break the desktop.
            up_ok, up_detail = self._xdotool(["mouseup", btn])
        if not up_ok:
            return {"ok": False, "error": up_detail, "phase": "mouseup"}

        position = self.pointer_position()
        if position is None:
            return {"ok": False, "error": "could not read the pointer position after the drag",
                    "requested": {"x": x2, "y": y2}}
        return {
            "ok": position == {"x": x2, "y": y2},
            "from": {"x": x1, "y": y1},
            "requested": {"x": x2, "y": y2},
            "position": position,
            "button": button,
            "steps": steps,
            "backend": "xdotool",
            **({} if position == {"x": x2, "y": y2} else
               {"error": f"pointer ended at {position}, not at {{'x': {x2}, 'y': {y2}}}"}),
        }

    # ------------------------------------------------------------------
    # Clipboard
    # ------------------------------------------------------------------
    def clipboard_get(self) -> dict[str, Any]:
        display = _display_env().get("DISPLAY")
        if display and shutil.which("xclip"):
            try:
                r = subprocess.run(  # noqa: S603
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=_display_env(),
                )
                if r.returncode == 0:
                    return {"text": r.stdout, "format": "text", "tool": "xclip"}
            except Exception:  # noqa: BLE001
                pass
        if display and shutil.which("xsel"):
            try:
                r = subprocess.run(  # noqa: S603
                    ["xsel", "--clipboard", "--output"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=_display_env(),
                )
                if r.returncode == 0:
                    return {"text": r.stdout, "format": "text", "tool": "xsel"}
            except Exception:  # noqa: BLE001
                pass
        if shutil.which("wl-paste"):
            try:
                r = subprocess.run(  # noqa: S603
                    ["wl-paste", "-n"], capture_output=True, text=True, timeout=5
                )
                if r.returncode == 0:
                    return {"text": r.stdout, "format": "text", "tool": "wl-paste"}
            except Exception:  # noqa: BLE001
                pass
        if shutil.which("xclip"):
            try:
                r = subprocess.run(  # noqa: S603
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=_display_env(),
                )
                if r.returncode == 0:
                    return {"text": r.stdout, "format": "text", "tool": "xclip"}
            except Exception:  # noqa: BLE001
                pass
        if shutil.which("xsel"):
            try:
                r = subprocess.run(  # noqa: S603
                    ["xsel", "--clipboard", "--output"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=_display_env(),
                )
                if r.returncode == 0:
                    return {"text": r.stdout, "format": "text", "tool": "xsel"}
            except Exception:  # noqa: BLE001
                pass
        return {
            "text": "",
            "format": "text",
            "ok": False,
            "error": "clipboard tool missing (install xclip, xsel, or wl-clipboard)",
        }

    def clipboard_set(self, text: str) -> dict[str, Any]:
        # Prefer X11 tools when DISPLAY is set — xclip must not use capture_output
        # (it daemonizes and hangs if stdout/stderr pipes stay open).
        display = _display_env().get("DISPLAY")
        if display and shutil.which("xclip"):
            try:
                r = subprocess.run(  # noqa: S603
                    ["xclip", "-selection", "clipboard", "-i"],
                    input=text.encode("utf-8"),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    env=_display_env(),
                )
                if r.returncode == 0:
                    return {"ok": True, "length": len(text), "tool": "xclip"}
            except Exception:  # noqa: BLE001
                pass
        if display and shutil.which("xsel"):
            try:
                r = subprocess.run(  # noqa: S603
                    ["xsel", "--clipboard", "--input"],
                    input=text.encode("utf-8"),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    env=_display_env(),
                )
                if r.returncode == 0:
                    return {"ok": True, "length": len(text), "tool": "xsel"}
            except Exception:  # noqa: BLE001
                pass
        if shutil.which("wl-copy"):
            try:
                r = subprocess.run(  # noqa: S603
                    ["wl-copy"],
                    input=text,
                    text=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                if r.returncode == 0:
                    return {"ok": True, "length": len(text), "tool": "wl-copy"}
            except Exception:  # noqa: BLE001
                pass
        if shutil.which("xclip"):
            try:
                r = subprocess.run(  # noqa: S603
                    ["xclip", "-selection", "clipboard", "-i"],
                    input=text.encode("utf-8"),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    env=_display_env(),
                )
                if r.returncode == 0:
                    return {"ok": True, "length": len(text), "tool": "xclip"}
            except Exception:  # noqa: BLE001
                pass
        if shutil.which("xsel"):
            try:
                r = subprocess.run(  # noqa: S603
                    ["xsel", "--clipboard", "--input"],
                    input=text.encode("utf-8"),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    env=_display_env(),
                )
                if r.returncode == 0:
                    return {"ok": True, "length": len(text), "tool": "xsel"}
            except Exception:  # noqa: BLE001
                pass
        return {
            "ok": False,
            "error": "clipboard tool missing (install xclip, xsel, or wl-clipboard)",
        }

    # ------------------------------------------------------------------
    # Display / screenshot
    # ------------------------------------------------------------------
    def list_displays(self) -> list[dict[str, Any]]:
        try:
            import mss  # type: ignore

            with mss.mss() as sct:
                out = []
                for i, mon in enumerate(sct.monitors[1:], start=0):
                    out.append(
                        {
                            "id": i,
                            "name": f"Display {i}",
                            "width": mon["width"],
                            "height": mon["height"],
                            "primary": i == 0,
                            "scale": 1.0,
                        }
                    )
                return out or [
                    {
                        "id": 0,
                        "name": "Primary",
                        "width": 1920,
                        "height": 1080,
                        "primary": True,
                        "scale": 1.0,
                    }
                ]
        except Exception:  # noqa: BLE001
            return [
                {
                    "id": 0,
                    "name": "Primary",
                    "width": 0,
                    "height": 0,
                    "primary": True,
                    "scale": 1.0,
                    "note": "mss unavailable — dimensions unknown",
                }
            ]

    def screenshot(self, display_id: int | None = None) -> dict[str, Any]:
        try:
            import base64
            import mss  # type: ignore
            from mss.tools import to_png  # type: ignore

            # mss uses X11 when DISPLAY is set
            os.environ.setdefault("DISPLAY", _display_env().get("DISPLAY", ":0"))
            with mss.mss() as sct:
                monitors = sct.monitors[1:]
                idx = display_id or 0
                if idx < 0 or idx >= len(monitors):
                    mon = sct.monitors[0]
                else:
                    mon = monitors[idx]
                shot = sct.grab(mon)
                png = to_png(shot.rgb, shot.size)
                return {
                    "ok": True,
                    "display_id": idx,
                    "format": "png",
                    "width": shot.width,
                    "height": shot.height,
                    "data_base64": base64.b64encode(png).decode("ascii"),
                }
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "supported": False,
                "error": "screenshot unsupported (install mss / Pillow+X11)",
                "detail": str(e),
                "display_id": display_id,
            }

    # ------------------------------------------------------------------
    # Filesystem (sandbox + optional allow paths; block ..)
    # ------------------------------------------------------------------
    def _safe_path(self, path: str) -> Path:
        norm = path.replace("\\", "/")
        parts = [x for x in norm.split("/") if x not in ("", ".")]
        if ".." in parts:
            raise PermissionError(f"Path traversal blocked: {path}")
        p = Path(norm)
        if not p.is_absolute():
            p = self.sandbox / p
        resolved = p.resolve()
        allowed_roots = [self.sandbox, *self.allow_paths]
        for root in allowed_roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        raise PermissionError(f"Path outside sandbox/allowlist: {path}")

    def fs_list(self, path: str) -> list[dict[str, Any]]:
        target = self._safe_path(path) if path not in (".", "") else self.sandbox
        if not target.exists():
            return []
        if not target.is_dir():
            raise NotADirectoryError(str(target))
        out = []
        for child in sorted(target.iterdir()):
            try:
                rel = str(child.relative_to(self.sandbox))
            except ValueError:
                rel = str(child)
            out.append(
                {
                    "name": child.name,
                    "path": rel,
                    "is_dir": child.is_dir(),
                    "size": child.stat().st_size if child.is_file() else 0,
                }
            )
        return out

    def fs_read(self, path: str, max_bytes: int = 65536) -> dict[str, Any]:
        import base64

        target = self._safe_path(path)
        data = target.read_bytes()[:max_bytes]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        return {
            "path": path,
            "size": len(data),
            "text": text,
            "base64": base64.b64encode(data).decode(),
        }

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

    # ------------------------------------------------------------------
    # Storage / network
    # ------------------------------------------------------------------
    def list_drives(self) -> list[dict[str, Any]]:
        if not self._psutil:
            return [{"mount": "/", "fs": "unknown", "total_gb": 0, "free_gb": 0}]
        out = []
        for p in self._psutil.disk_partitions(all=False):
            try:
                usage = self._psutil.disk_usage(p.mountpoint)
                out.append(
                    {
                        "mount": p.mountpoint,
                        "device": p.device,
                        "fs": p.fstype,
                        "total_gb": round(usage.total / 1e9, 1),
                        "free_gb": round(usage.free / 1e9, 1),
                    }
                )
            except (PermissionError, OSError):
                continue
        return out

    def network_interfaces(self) -> list[dict[str, Any]]:
        if not self._psutil:
            return []
        stats = {}
        try:
            stats = self._psutil.net_if_stats()
        except Exception:  # noqa: BLE001
            pass
        out = []
        for name, addrs in self._psutil.net_if_addrs().items():
            st = stats.get(name)
            out.append(
                {
                    "name": name,
                    "addresses": [a.address for a in addrs],
                    "up": bool(st.isup) if st else True,
                }
            )
        return out

    def network_connections(self) -> list[dict[str, Any]]:
        if not self._psutil:
            return []
        out = []
        try:
            conns = self._psutil.net_connections(kind="inet")
        except (PermissionError, self._psutil.AccessDenied):
            return []
        for c in conns[:200]:
            out.append(
                {
                    "local": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "",
                    "remote": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "",
                    "status": c.status,
                    "pid": c.pid,
                }
            )
        return out

    # ------------------------------------------------------------------
    # Network probes: routes, DNS, ping
    # ------------------------------------------------------------------
    def list_routes(self) -> list[dict[str, Any]]:
        """The kernel routing table, read from /proc — no binary required.

        `ip route` would need iproute2, which is not installed on every system
        (it is absent from the container this was written in). /proc/net/route
        is the kernel's own view and is always there on Linux, so this works on
        a minimal host instead of returning an empty list that reads as "no
        routes" when it really means "no tool".
        """
        path = Path("/proc/net/route")
        if not path.is_file():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

        out: list[dict[str, Any]] = []
        for line in lines[1:]:  # the first line is the column header
            fields = line.split()
            if len(fields) < 8:
                continue
            try:
                destination = _hex_le_to_ip(fields[1])
                gateway = _hex_le_to_ip(fields[2])
                mask = _hex_le_to_ip(fields[7])
                flags = int(fields[3], 16)
                metric = int(fields[6])
            except (ValueError, OSError):
                continue
            out.append({
                "interface": fields[0],
                "destination": destination,
                "gateway": gateway,
                "netmask": mask,
                "metric": metric,
                # 0.0.0.0/0 is the default route — the one an operator asks
                # about first, so it is labelled rather than left to be inferred
                # from two zero strings.
                "default": destination == "0.0.0.0" and mask == "0.0.0.0",
                "up": bool(flags & 0x0001),  # RTF_UP
                "source": "/proc/net/route",
            })
        return out

    def dns_resolve(self, host: str) -> dict[str, Any]:
        return _dns.resolve(host)

    def dns_reverse(self, address: str) -> dict[str, Any]:
        return _dns.reverse(address)

    def ping(self, host: str, count: int = 2, timeout: int = 2) -> dict[str, Any]:
        """ICMP echo via the `ping` binary, as argv and bounded.

        Bounded matters: `ping` with no count runs until something kills it, and
        this is reachable through an HTTP endpoint. Both the per-reply timeout
        and an overall subprocess timeout are set, so the request cannot outlive
        its own limits even if the binary ignores one of them.
        """
        try:
            host = validate_host(host)
            count, timeout = validate_ping(count, timeout)
        except NetworkRejected as exc:
            return {"ok": False, "error": str(exc), "host": host}
        if not shutil.which("ping"):
            return {"ok": False, "error": "ping binary not installed (iputils-ping)",
                    "host": host, "available": False}
        try:
            r = subprocess.run(  # noqa: S603
                ["ping", "-c", str(count), "-W", str(timeout), host],
                capture_output=True,
                text=True,
                # Enough for every reply to time out, plus room to exit.
                timeout=count * timeout + 5,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "ping did not finish within its own limits",
                    "host": host, "count": count}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "host": host}

        return {
            # Exit code 0 means at least one reply came back. The count is
            # reported too, because "1 of 4 replied" is a different answer from
            # "all did", and the caller should not have to guess which they got.
            "ok": r.returncode == 0,
            "host": host,
            "transmitted": count,
            "received": _parse_ping_received(r.stdout),
            "exit_code": r.returncode,
            "output": r.stdout[:4000],
        }

    # ------------------------------------------------------------------
    # Services (systemctl --user and system)
    # ------------------------------------------------------------------
    def list_services(self) -> list[dict[str, Any]]:
        return _lsvc.list_services()

    def control_service(self, name: str, action: str, scope: str = "user") -> dict[str, Any]:
        return _lsvc.control_service(name, action, scope=scope)

    # ------------------------------------------------------------------
    # Audio (pactl / wpctl)
    # ------------------------------------------------------------------
    def audio_devices(self) -> list[dict[str, Any]]:
        return _laudio.list_devices()

    def audio_volume(self) -> dict[str, Any]:
        return _laudio.get_volume()

    def audio_set_volume(self, percent: int) -> dict[str, Any]:
        return _laudio.set_volume(percent)

    def audio_set_mute(self, muted: bool) -> dict[str, Any]:
        return _laudio.set_mute(muted)

    # ------------------------------------------------------------------
    # Devices / printers / users
    # ------------------------------------------------------------------
    def list_devices(self) -> list[dict[str, Any]]:
        """Block devices from `/sys/block`, reporting only what was measured.

        `status` used to be the literal `"ok"` on every row — a health claim
        nothing had checked. The entry in `/sys/block` supports exactly one
        claim, that the device is present, so that is what it now says.
        `media` comes from `removable`, a file the kernel actually maintains,
        and falls back to `"unknown"` when it cannot be read rather than
        guessing "fixed".
        """
        out: list[dict[str, Any]] = []
        sys_block = Path("/sys/block")
        if not sys_block.is_dir():
            return out
        for d in sorted(sys_block.iterdir())[:50]:
            media = "unknown"
            try:
                media = "removable" if (d / "removable").read_text().strip() == "1" else "fixed"
            except OSError:
                pass
            out.append({
                "id": d.name,
                "name": d.name,
                "type": "block",
                "media": media,
                "status": "present",
            })
        return out

    def list_printers(self) -> list[dict[str, Any]]:
        """Stampanti via `lpstat -a`.

        `lpstat` assente non e' un errore: e' una capability che manca, e la
        riporta il flag `printers`. Un `lpstat` che invece c'e' e fallisce (CUPS
        giu', timeout) e' un'altra cosa e lo dice: rispondere `[]` a un timeout
        affermerebbe che non ci sono stampanti, che e' esattamente il difetto
        che il contratto D3 toglie.
        """
        if not shutil.which("lpstat"):
            return []
        try:
            r = subprocess.run(  # noqa: S603
                ["lpstat", "-a"], capture_output=True, text=True, timeout=5
            )
        except Exception as exc:  # noqa: BLE001
            raise DiscoveryFailed(f"lpstat -a fallita: {exc}") from exc
        printers = []
        for line in r.stdout.splitlines():
            name = line.split()[0] if line.split() else ""
            if name:
                printers.append({"name": name, "status": "idle", "default": False})
        return printers

    def list_users(self) -> list[dict[str, Any]]:
        if self._psutil:
            try:
                users = []
                for u in self._psutil.users():
                    users.append(
                        {
                            "username": u.name,
                            "domain": platform.node(),
                            "admin": u.name == "root",
                            "terminal": getattr(u, "terminal", "") or "",
                        }
                    )
                if users:
                    return users
            except Exception:  # noqa: BLE001
                pass
        return [
            {
                "username": os.environ.get("USER", ""),
                "domain": platform.node(),
                "admin": os.geteuid() == 0 if hasattr(os, "geteuid") else False,
            }
        ]

    def list_sessions(self) -> list[dict[str, Any]]:
        if shutil.which("loginctl"):
            try:
                from windows_os_api.core.security.privilege import parse_loginctl_sessions

                r = subprocess.run(  # noqa: S603
                    ["loginctl", "list-sessions", "--no-legend"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                parsed = parse_loginctl_sessions(r.stdout or "")
                if parsed:
                    return parsed
            except Exception:  # noqa: BLE001
                pass
        # Fallback derivato dagli utenti connessi: NON e' un'enumerazione di
        # sessioni. `state` era il letterale "Active" — uno stato che nessuno
        # aveva misurato, appiccicato a righe sintetizzate. `source` dice gia'
        # da dove vengono; ora anche lo stato dice la verita'.
        users = self.list_users()
        return [
            {
                "id": i + 1,
                "user": u.get("username", ""),
                "state": "unknown",
                "client": u.get("terminal") or "local",
                "source": "psutil",
                "derived": True,
            }
            for i, u in enumerate(users)
        ]

    def session_info(self) -> dict[str, Any]:
        """Current display session + seats/sessions snapshot."""
        session = detect_session()
        return {
            "session": session,
            "sessions": self.list_sessions(),
            "users": self.list_users(),
            "capability_flags": self.capability_flags(),
        }

    def elevate(self, argv: list[str], *, auth_has_admin: bool = False, subject: str = "system") -> dict[str, Any]:
        from windows_os_api.core.security.privilege import attempt_elevation

        return attempt_elevation(argv, auth_has_admin=auth_has_admin, subject=subject)

    # ------------------------------------------------------------------
    # Registry compat → JSON store
    # ------------------------------------------------------------------
    def _load_registry(self) -> dict[str, Any]:
        try:
            return json.loads(self._registry_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_registry(self, data: dict[str, Any]) -> None:
        self._registry_file.parent.mkdir(parents=True, exist_ok=True)
        self._registry_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def registry_read(self, path: str, name: str | None = None) -> dict[str, Any]:
        data = self._load_registry()
        key = data.get(path)
        if key is None:
            return {
                "ok": False,
                "error": "key not found",
                "path": path,
                "store": str(self._registry_file),
                "note": "Linux registry compat JSON store (not Windows registry)",
            }
        if name is None:
            return {"ok": True, "path": path, "values": dict(key), "store": str(self._registry_file)}
        if name not in key:
            return {"ok": False, "error": "value not found", "path": path, "name": name}
        return {"ok": True, "path": path, "name": name, "value": key[name]}

    def registry_write(self, path: str, name: str, value: Any) -> dict[str, Any]:
        data = self._load_registry()
        if path not in data or not isinstance(data[path], dict):
            data[path] = {}
        data[path][name] = value
        self._save_registry(data)
        return {
            "ok": True,
            "path": path,
            "name": name,
            "value": value,
            "store": str(self._registry_file),
        }

    # ------------------------------------------------------------------
    # Terminal
    # ------------------------------------------------------------------
    def terminal_execute(self, command: str, policy: str = "ALLOW") -> dict[str, Any]:
        policy = policy.upper()
        if policy == "DENY":
            return {"ok": False, "policy": "DENY", "error": "denied", "command": command}
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
        try:
            r = subprocess.run(  # noqa: S603
                argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.sandbox),
            )
            return {
                "ok": r.returncode == 0,
                "policy": policy,
                "stdout": r.stdout[:8000],
                "stderr": r.stderr[:2000],
                "exit_code": r.returncode,
                "real": True,
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "policy": policy}
