"""Process management."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend

def list_processes() -> list[dict[str, Any]]:
    return get_backend().list_processes()

def get_process(pid: int) -> dict[str, Any] | None:
    return get_backend().get_process(pid)

def start_process(command: str, args: list[str] | None = None) -> dict[str, Any]:
    return get_backend().start_process(command, args)

def terminate_process(pid: int) -> dict[str, Any]:
    return get_backend().terminate_process(pid)
