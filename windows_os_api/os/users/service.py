"""Users and sessions."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend

def list_users() -> list[dict[str, Any]]:
    return get_backend().list_users()

def list_sessions() -> list[dict[str, Any]]:
    return get_backend().list_sessions()
