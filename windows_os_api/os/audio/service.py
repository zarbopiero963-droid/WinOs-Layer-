"""Audio devices / volume / mute."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend

def devices() -> list[dict[str, Any]]:
    return get_backend().audio_devices()

def volume() -> dict[str, Any]:
    return get_backend().audio_volume()

def set_volume(percent: int) -> dict[str, Any]:
    b = get_backend()
    if hasattr(b, "audio_set_volume"):
        return b.audio_set_volume(percent)
    return {"ok": False, "error": "set_volume not supported by backend"}

def set_mute(muted: bool) -> dict[str, Any]:
    b = get_backend()
    if hasattr(b, "audio_set_mute"):
        return b.audio_set_mute(muted)
    return {"ok": False, "error": "set_mute not supported by backend"}
