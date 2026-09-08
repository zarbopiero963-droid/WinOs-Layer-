"""Audio devices / volume / mute."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend
from windows_os_api.os.capability import discover, unsupported

def devices() -> dict[str, Any]:
    """Device audio, col contratto `supported` (decisioni owner D3-A e D4-B).

    Su Windows questo risponde `supported: false`: l'audio richiederebbe Core
    Audio COM (`pycaw`), che l'owner ha deciso di non aggiungere adesso (D4-B).
    Prima rispondeva `200` con lista vuota, cioe' «questa macchina non ha
    dispositivi audio» — un'affermazione falsa su qualunque PC.
    """
    b = get_backend()
    return discover(b, "audio", "devices", b.audio_devices)

def volume() -> dict[str, Any]:
    """Volume corrente, o la dichiarazione che qui non si puo' leggere.

    `{"volume": null, "muted": null}` diceva «il volume e' nullo». Non lo e':
    non lo sappiamo, ed e' un'altra cosa.
    """
    b = get_backend()
    flags = getattr(b, "capability_flags", dict)
    try:
        supported = dict(flags()).get("audio") is not False if callable(flags) else True
    except Exception:  # noqa: BLE001
        supported = True
    if not supported:
        out = unsupported(b, "audio", "volume")
        out["muted"] = None
        return out
    result = dict(b.audio_volume())
    result.setdefault("supported", True)
    return result

def set_volume(percent: int) -> dict[str, Any]:
    b = get_backend()
    if hasattr(b, "audio_set_volume"):
        return b.audio_set_volume(percent)
    return {"ok": False, "error": "set_volume not supported by backend", "supported": False}

def set_mute(muted: bool) -> dict[str, Any]:
    b = get_backend()
    if hasattr(b, "audio_set_mute"):
        return b.audio_set_mute(muted)
    return {"ok": False, "error": "set_mute not supported by backend", "supported": False}
