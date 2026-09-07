"""Audio devices / volume."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend

def devices() -> list[dict[str, Any]]:
    return get_backend().audio_devices()

def volume() -> dict[str, Any]:
    return get_backend().audio_volume()
