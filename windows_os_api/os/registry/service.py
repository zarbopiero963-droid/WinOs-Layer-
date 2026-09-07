"""Registry read/write with policy."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend

def read(path: str, name: str | None = None) -> dict[str, Any]:
    return get_backend().registry_read(path, name)

def write(path: str, name: str, value: Any) -> dict[str, Any]:
    return get_backend().registry_write(path, name, value)
