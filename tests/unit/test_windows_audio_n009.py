"""N009 / H63-N009 — Windows audio read (devices/default/volume/mute).

Mocks only. Never mutates a real device (N010). No pycaw.
"""
from __future__ import annotations

import pytest

from windows_os_api.backends import windows_audio as wa
from windows_os_api.os.capability import (
    CAPABILITY_NOT_SUPPORTED,
    CAPABILITY_UNAVAILABLE,
    discover,
    unsupported,
)


class _Session:
    def __init__(self, devices=None, volume=None, *, boom_list=False, boom_vol=False):
        self._devices = list(devices or [])
        self._volume = volume
        self.boom_list = boom_list
        self.boom_vol = boom_vol

    def list_endpoints(self):
        if self.boom_list:
            raise RuntimeError("enumerator exploded")
        return list(self._devices)

    def read_volume(self, device_id=None):
        if self.boom_vol:
            raise RuntimeError("volume exploded")
        if self._volume is None:
            return {
                "ok": False,
                "supported": True,
                "volume": None,
                "muted": None,
                "code": "device_absent",
                "error": "no default render endpoint",
                "backend": "fake-wasapi",
            }
        out = dict(self._volume)
        if device_id:
            out["device_id"] = device_id
        return out


def test_list_devices_returns_endpoints_and_default():
    session = _Session(
        devices=[
            {"id": "dev-out", "name": "Speakers", "type": "output", "default": True, "backend": "wasapi"},
            {"id": "dev-in", "name": "Mic", "type": "input", "default": True, "backend": "wasapi"},
        ]
    )
    rows = wa.list_devices(session=session)
    assert [r["id"] for r in rows] == ["dev-out", "dev-in"]
    assert rows[0]["default"] is True


def test_empty_list_is_honest_zero_not_unsupported():
    rows = wa.list_devices(session=_Session(devices=[]))
    assert rows == []


def test_missing_session_is_unavailable_not_unsupported():
    with pytest.raises(wa.AudioSessionUnavailable):
        wa.list_devices(session=None)


def test_enumerator_failure_is_discovery_failed():
    with pytest.raises(Exception) as ei:
        wa.list_devices(session=_Session(boom_list=True))
    from windows_os_api.os.capability import DiscoveryFailed

    assert isinstance(ei.value, DiscoveryFailed)


def test_volume_read_verified():
    session = _Session(volume={"ok": True, "supported": True, "volume": 42, "muted": False, "device_id": "dev-out", "verified": True, "backend": "wasapi"})
    out = wa.get_volume(session=session)
    assert out["ok"] is True
    assert out["volume"] == 42
    assert out["muted"] is False
    assert out["verified"] is True


def test_volume_device_absent_distinct_from_session_missing():
    absent = wa.get_volume(session=_Session(volume=None))
    assert absent["code"] == "device_absent"
    assert absent["volume"] is None
    with pytest.raises(wa.AudioSessionUnavailable):
        wa.get_volume(session=None)


def test_windows_backend_no_longer_lists_audio_as_never_implemented():
    from windows_os_api.backends.windows import WindowsBackend

    assert "audio" not in WindowsBackend.NOT_IMPLEMENTED


def test_discover_unavailable_when_flag_false_and_not_in_never():
    class _B:
        name = "windows"
        NOT_IMPLEMENTED = frozenset()

        def capability_flags(self):
            return {"audio": False}

        def audio_devices(self):
            raise AssertionError("discover must not call when flag is False")

    out = discover(_B(), "audio", "devices", _B().audio_devices)
    assert out["supported"] is False
    assert out["error_code"] == CAPABILITY_UNAVAILABLE
    assert out["devices"] == []


def test_unsupported_volume_is_unavailable_not_not_supported():
    class _B:
        name = "windows"
        NOT_IMPLEMENTED = frozenset()

        def capability_flags(self):
            return {"audio": False}

    out = unsupported(_B(), "audio", "volume")
    assert out["error_code"] == CAPABILITY_UNAVAILABLE
    assert out["error_code"] != CAPABILITY_NOT_SUPPORTED


def test_discover_empty_list_when_supported():
    class _B:
        name = "windows"
        NOT_IMPLEMENTED = frozenset()

        def capability_flags(self):
            return {"audio": True}

        def audio_devices(self):
            return []

    out = discover(_B(), "audio", "devices", _B().audio_devices)
    assert out["supported"] is True
    assert out["devices"] == []
    assert "error_code" not in out


def test_try_open_session_does_not_raise_on_linux():
    session = wa.try_open_session()
    # On this Linux box COM is absent — None is honest unavailable.
    assert session is None or hasattr(session, "list_endpoints")


def test_n009_read_path_still_present_alongside_n010_mutations():
    """N009 read stays; N010 publishes set_volume/set_mute on WindowsBackend."""
    from windows_os_api.backends.windows import WindowsBackend

    assert hasattr(WindowsBackend, "audio_volume")
    assert hasattr(WindowsBackend, "audio_devices")
    assert hasattr(WindowsBackend, "audio_set_volume")
    assert hasattr(WindowsBackend, "audio_set_mute")
