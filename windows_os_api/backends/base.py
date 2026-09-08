"""Abstract OS backend protocol shared by WindowsBackend, LinuxBackend, FakeBackend.

Implementations must expose the same method surface so OS service modules remain
backend-agnostic. Behavioral notes:

- WindowsBackend: real Win32 (guarded; raises off win32).
- LinuxBackend: real Linux process/FS/system/network; optional AT-SPI / wmctrl /
  clipboard / pactl degrade gracefully. Never returns Fake Contoso CRM data.
- FakeBackend: deterministic in-memory fixtures (Contoso CRM UI tree) for adapter
  E2E and CI — select explicitly via WINOS_BACKEND=fake.

Factory resolve_backend() / get_backend():
  WINOS_BACKEND=auto → windows on win32, linux on Linux (fake only if forced).
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class OSBackend(Protocol):
    """Shared backend contract for OS Layer services."""

    name: str

    # System
    def get_system_info(self) -> dict[str, Any]:
        """Hostname, OS name/version, arch, backend id — real platform strings."""
        ...

    def get_resources(self) -> dict[str, Any]:
        """CPU / memory / disk snapshot."""
        ...

    def get_uptime(self) -> dict[str, Any]:
        """Boot time and uptime seconds."""
        ...

    def power_action(self, action: str) -> dict[str, Any]:
        """sleep/hibernate/shutdown/reboot/lock — usually gated / stubbed."""
        ...

    # Processes
    def list_processes(self) -> list[dict[str, Any]]:
        """List processes (real via psutil on Windows/Linux)."""
        ...

    def get_process(self, pid: int) -> dict[str, Any] | None:
        """Single process by PID."""
        ...

    def start_process(self, command: str, args: list[str] | None = None) -> dict[str, Any]:
        """Start a process — MUST actually spawn on Windows/Linux backends."""
        ...

    def terminate_process(self, pid: int) -> dict[str, Any]:
        """Terminate / kill a process."""
        ...

    # Apps
    def discover_apps(self) -> list[dict[str, Any]]:
        """Installed / discoverable applications."""
        ...

    # Windows
    def list_windows(self) -> list[dict[str, Any]]:
        """Top-level windows (Win32 EnumWindows / wmctrl / xdotool)."""
        ...

    def get_window(self, hwnd: int) -> dict[str, Any] | None:
        ...

    def focus_window(self, hwnd: int) -> dict[str, Any]:
        ...

    def close_window(self, hwnd: int) -> dict[str, Any]:
        ...

    # Window geometry / state.
    #
    # These four return the geometry the OS reports *after* the operation, not
    # only `ok: True`. A window manager is free to refuse, clamp or quantise a
    # request — openbox adds a frame offset, a terminal snaps to character
    # cells — so the request is not the result, and the caller is told which is
    # which. `ok` means the effect was observed, not that a command exited 0.
    def window_geometry(self, hwnd: int) -> dict[str, int] | None:
        """`{x, y, width, height}` as the OS reports it, or None if the window is gone.

        The read primitive the other five verify themselves against.
        """
        ...

    def move_window(self, hwnd: int, x: int, y: int) -> dict[str, Any]:
        ...

    def resize_window(self, hwnd: int, width: int, height: int) -> dict[str, Any]:
        ...

    def minimize_window(self, hwnd: int) -> dict[str, Any]:
        ...

    def maximize_window(self, hwnd: int) -> dict[str, Any]:
        ...

    def restore_window(self, hwnd: int) -> dict[str, Any]:
        """Undo minimize/maximize. Without it the other two are a one-way door."""
        ...

    # UI tree
    def get_ui_tree(self, hwnd: int | None = None) -> dict[str, Any]:
        """UI Automation (Windows) / AT-SPI (Linux) / Fake Contoso tree."""
        ...

    # Input
    def mouse_move(self, x: int, y: int) -> dict[str, Any]:
        ...

    def mouse_click(self, x: int, y: int, button: str = "left") -> dict[str, Any]:
        ...

    def key_press(self, key: str, modifiers: list[str] | None = None) -> dict[str, Any]:
        ...

    def type_text(self, text: str) -> dict[str, Any]:
        ...

    # Input: the rest of the primitives.
    #
    # Unlike window geometry, a keystroke or a click has no readback — once the
    # OS accepts the event it belongs to whatever window has focus, and nothing
    # reports what that window did with it. So `ok` here means the OS accepted
    # the event, checked rather than assumed. Delivery is proven in the tests,
    # which read the events back from `xev` on Linux.
    #
    # The pointer IS readable, so mouse_move and mouse_drag verify where it
    # ended up and report `position` alongside `requested`.
    def double_click(self, x: int, y: int, button: str = "left") -> dict[str, Any]:
        ...

    def scroll(self, direction: str = "down", amount: int = 3,
               x: int | None = None, y: int | None = None) -> dict[str, Any]:
        ...

    def key_down(self, key: str) -> dict[str, Any]:
        """Press and hold. The matching key_up is the caller's responsibility."""
        ...

    def key_up(self, key: str) -> dict[str, Any]:
        ...

    def hotkey(self, keys: list[str]) -> dict[str, Any]:
        """A chord: all keys down together, then released."""
        ...

    def mouse_drag(self, x1: int, y1: int, x2: int, y2: int,
                   button: str = "left", *, steps: int = 10) -> dict[str, Any]:
        ...

    def pointer_position(self) -> dict[str, int] | None:
        """`{x, y}` as the OS reports it, or None when it cannot be read."""
        ...

    # Clipboard
    def clipboard_get(self) -> dict[str, Any]:
        ...

    def clipboard_set(self, text: str) -> dict[str, Any]:
        ...

    # Display
    def list_displays(self) -> list[dict[str, Any]]:
        ...

    def screenshot(self, display_id: int | None = None) -> dict[str, Any]:
        ...

    # Filesystem (sandbox policy on Fake/Linux; Windows uses caller paths)
    def fs_list(self, path: str) -> list[dict[str, Any]]:
        ...

    def fs_read(self, path: str, max_bytes: int = 65536) -> dict[str, Any]:
        ...

    def fs_write(self, path: str, content: str) -> dict[str, Any]:
        ...

    def fs_delete(self, path: str) -> dict[str, Any]:
        ...

    # Storage
    def list_drives(self) -> list[dict[str, Any]]:
        ...

    # Network
    def network_interfaces(self) -> list[dict[str, Any]]:
        ...

    def network_connections(self) -> list[dict[str, Any]]:
        ...

    def list_routes(self) -> list[dict[str, Any]]:
        """The routing table. Entries carry a `default` flag for 0.0.0.0/0."""
        ...

    def dns_resolve(self, host: str) -> dict[str, Any]:
        """Forward lookup. Delegates to `os/network/dns.py` on every backend —
        `socket.getaddrinfo` is the same call everywhere, so three copies could
        only differ by drifting."""
        ...

    def dns_reverse(self, address: str) -> dict[str, Any]:
        ...

    def ping(self, host: str, count: int = 2, timeout: int = 2) -> dict[str, Any]:
        """ICMP echo, bounded. `ok` means at least one reply came back;
        `received` says how many, because "1 of 4" is a different answer from
        "all of them"."""
        ...

    # Services
    def list_services(self) -> list[dict[str, Any]]:
        ...

    def control_service(self, name: str, action: str) -> dict[str, Any]:
        ...

    # Audio
    def audio_devices(self) -> list[dict[str, Any]]:
        ...

    def audio_volume(self) -> dict[str, Any]:
        ...

    # Devices
    def list_devices(self) -> list[dict[str, Any]]:
        ...

    # Printers
    def list_printers(self) -> list[dict[str, Any]]:
        ...

    # Users / sessions
    def list_users(self) -> list[dict[str, Any]]:
        ...

    def list_sessions(self) -> list[dict[str, Any]]:
        ...

    # Registry (real on Windows; JSON compat store on Linux)
    def registry_read(self, path: str, name: str | None = None) -> dict[str, Any]:
        ...

    def registry_write(self, path: str, name: str, value: Any) -> dict[str, Any]:
        ...

    # Terminal
    def terminal_execute(self, command: str, policy: str = "ALLOW") -> dict[str, Any]:
        ...
