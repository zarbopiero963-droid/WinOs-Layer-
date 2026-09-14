"""Audio devices / volume / mute."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend
from windows_os_api.os.capability import discover, unsupported

def devices() -> dict[str, Any]:
    """Device audio, col contratto `supported` (D3-A; N009 / D4).

    Windows implements WASAPI read (comtypes, no pycaw). ``supported: false``
    with ``CAPABILITY_UNAVAILABLE`` means the session could not be opened on
    this machine — not that the backend never implements audio. An empty
    ``devices`` list with ``supported: true`` means the enumerator ran and
    found no active endpoints.
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
    """N010: validate 0–100 before backend; backend owns restore-on-failure."""
    if isinstance(percent, bool) or not isinstance(percent, int):
        return {
            "ok": False,
            "supported": True,
            "volume": None,
            "muted": None,
            "verified": False,
            "code": "invalid_volume",
            "error": f"volume must be an int in 0..100 inclusive, got {percent!r}",
        }
    if percent < 0 or percent > 100:
        return {
            "ok": False,
            "supported": True,
            "volume": None,
            "muted": None,
            "verified": False,
            "code": "invalid_volume",
            "error": f"volume must be in 0..100 inclusive, got {percent}",
        }
    b = get_backend()
    if hasattr(b, "audio_set_volume"):
        return b.audio_set_volume(percent)
    return {"ok": False, "error": "set_volume not supported by backend", "supported": False}

def set_mute(muted: bool) -> dict[str, Any]:
    """N010: require bool; backend owns restore-on-failure."""
    if not isinstance(muted, bool):
        return {
            "ok": False,
            "supported": True,
            "volume": None,
            "muted": None,
            "verified": False,
            "code": "invalid_mute",
            "error": f"muted must be bool, got {type(muted).__name__}",
        }
    b = get_backend()
    if hasattr(b, "audio_set_mute"):
        return b.audio_set_mute(muted)
    return {"ok": False, "error": "set_mute not supported by backend", "supported": False}
