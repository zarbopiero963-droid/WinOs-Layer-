"""Process management."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend
from windows_os_api.os.capability import discover

def list_processes() -> dict[str, Any]:
    """Processi, col contratto `supported` (D3 / N004).

    Usa il flag backend `processes` (False senza psutil → UNAVAILABLE / NOT_SUPPORTED
    secondo NOT_IMPLEMENTED). Chiave `processes` invariata (additivo).
    """
    b = get_backend()
    return discover(b, "processes", "processes", b.list_processes)

def get_process(pid: int) -> dict[str, Any] | None:
    return get_backend().get_process(pid)

def start_process(command: str, args: list[str] | None = None) -> dict[str, Any]:
    return get_backend().start_process(command, args)

def terminate_process(pid: int) -> dict[str, Any]:
    return get_backend().terminate_process(pid)
