"""N010 / H63-N010 — Windows audio volume/mute mutations with restore.

Mocks only. No pycaw. Never touches a real WASAPI device on Linux CI.
"""
from __future__ import annotations

import pytest

from windows_os_api.backends import windows_audio as wa
from windows_os_api.os.audio import service as audio_svc


class _MutSession:
    """Injectable session: tracks volume/mute and can boom on set/readback."""

    def __init__(
        self,
        *,
        volume: int = 40,
        muted: bool = False,
        boom_set_volume: bool = False,
        boom_set_mute: bool = False,
        boom_read_after: int = 0,
        deny_set_volume: bool = False,
        deny_set_mute: bool = False,
        mismatch_volume: bool = False,
        mismatch_mute: bool = False,
        device_absent: bool = False,
    ):
        self.volume = volume
        self.muted = muted
        self.boom_set_volume = boom_set_volume
        self.boom_set_mute = boom_set_mute
        self.boom_read_after = boom_read_after
        self._reads = 0
        self.deny_set_volume = deny_set_volume
        self.deny_set_mute = deny_set_mute
        self.mismatch_volume = mismatch_volume
        self.mismatch_mute = mismatch_mute
        self.device_absent = device_absent
        self.set_volume_calls: list[tuple] = []
        self.set_mute_calls: list[tuple] = []

    def list_endpoints(self):
        return []

    def read_volume(self, device_id=None):
        self._reads += 1
        if self.device_absent:
            return {
                "ok": False,
                "supported": True,
                "volume": None,
                "muted": None,
                "code": "device_absent",
                "error": "no default render endpoint",
                "backend": "fake-wasapi",
            }
        # After first successful capture, optional boom on verify readback.
        if self.boom_read_after and self._reads > self.boom_read_after:
            raise RuntimeError("readback exploded")
        return {
            "ok": True,
            "supported": True,
            "volume": self.volume,
            "muted": self.muted,
            "device_id": device_id or "dev-out",
            "verified": True,
            "backend": "fake-wasapi",
        }

    def set_volume(self, percent, device_id=None):
        self.set_volume_calls.append((percent, device_id))
        if self.deny_set_volume:
            raise PermissionError("access denied")
        if self.boom_set_volume:
            # One-shot partial write then boom so restore can succeed.
            self.boom_set_volume = False
            self.volume = int(percent)
            raise RuntimeError("SetMasterVolumeLevelScalar failed")
        if self.mismatch_volume:
            # One-shot wrong value so verify_mismatch triggers restore.
            self.mismatch_volume = False
            self.volume = (int(percent) + 7) % 101
            return
        self.volume = int(percent)

    def set_mute(self, muted, device_id=None):
        self.set_mute_calls.append((muted, device_id))
        if self.deny_set_mute:
            raise PermissionError("access denied")
        if self.boom_set_mute:
            self.boom_set_mute = False
            self.muted = bool(muted)
            raise RuntimeError("SetMute failed")
        if self.mismatch_mute:
            self.mismatch_mute = False
            self.muted = not bool(muted)
            return
        self.muted = bool(muted)


def test_set_volume_success_with_readback():
    s = _MutSession(volume=40, muted=False)
    out = wa.set_volume(session=s, percent=55)
    assert out["ok"] is True
    assert out["volume"] == 55
    assert out["verified"] is True
    assert out["muted"] is False
    assert s.volume == 55
    assert len(s.set_volume_calls) == 1


def test_set_mute_toggle_success():
    s = _MutSession(volume=40, muted=False)
    out = wa.set_mute(session=s, muted=True)
    assert out["ok"] is True
    assert out["muted"] is True
    assert out["verified"] is True
    assert s.muted is True


def test_invalid_volume_range_does_not_mutate():
    s = _MutSession(volume=40)
    for bad in (-1, 101, 1000):
        out = wa.set_volume(session=s, percent=bad)
        assert out["ok"] is False
        assert out["code"] == "invalid_volume"
        assert out["verified"] is False
        assert s.set_volume_calls == []
        assert s.volume == 40


def test_invalid_volume_type_does_not_mutate():
    s = _MutSession(volume=40)
    for bad in (True, 50.5, "50", None):
        out = wa.set_volume(session=s, percent=bad)
        assert out["ok"] is False
        assert out["code"] == "invalid_volume"
        assert s.set_volume_calls == []
        assert s.volume == 40


def test_boundary_0_and_100_accepted():
    s = _MutSession(volume=50)
    assert wa.set_volume(session=s, percent=0)["ok"] is True
    assert s.volume == 0
    assert wa.set_volume(session=s, percent=100)["ok"] is True
    assert s.volume == 100


def test_session_none_raises_unavailable():
    with pytest.raises(wa.AudioSessionUnavailable):
        wa.set_volume(session=None, percent=50)
    with pytest.raises(wa.AudioSessionUnavailable):
        wa.set_mute(session=None, muted=True)


def test_set_volume_boom_then_restore():
    s = _MutSession(volume=40, muted=False, boom_set_volume=True)
    out = wa.set_volume(session=s, percent=80)
    assert out["ok"] is False
    assert out["code"] == "set_failed"
    assert out["verified"] is False
    assert out.get("restored") is True
    # Prior 40/False restored after boom (which had written 80).
    assert s.volume == 40
    assert s.muted is False


def test_set_mute_boom_then_restore():
    s = _MutSession(volume=40, muted=False, boom_set_mute=True)
    out = wa.set_mute(session=s, muted=True)
    assert out["ok"] is False
    assert out["code"] == "set_failed"
    assert out.get("restored") is True
    assert s.muted is False
    assert s.volume == 40


def test_verify_mismatch_restores_prior():
    s = _MutSession(volume=40, muted=False, mismatch_volume=True)
    out = wa.set_volume(session=s, percent=70)
    assert out["ok"] is False
    assert out["code"] == "verify_mismatch"
    assert out.get("restored") is True
    assert s.volume == 40


def test_deny_set_volume_restores_and_leaves_prior():
    s = _MutSession(volume=40, muted=True, deny_set_volume=True)
    out = wa.set_volume(session=s, percent=90)
    assert out["ok"] is False
    assert out["code"] == "set_failed"
    assert "denied" in out["error"].lower() or "access" in out["error"].lower()
    # Restore re-applies prior; state unchanged from caller POV.
    assert s.volume == 40
    assert s.muted is True


def test_device_absent_no_mutate():
    s = _MutSession(device_absent=True)
    out = wa.set_volume(session=s, percent=50)
    assert out["ok"] is False
    assert out["code"] == "device_absent"
    assert s.set_volume_calls == []


def test_invalid_mute_type():
    s = _MutSession()
    out = wa.set_mute(session=s, muted="yes")  # type: ignore[arg-type]
    assert out["ok"] is False
    assert out["code"] == "invalid_mute"
    assert s.set_mute_calls == []


def test_windows_backend_wires_mutations_honest_unavailable():
    from windows_os_api.backends.windows import WindowsBackend

    assert callable(WindowsBackend.audio_set_volume)
    assert callable(WindowsBackend.audio_set_mute)
    b = WindowsBackend.__new__(WindowsBackend)
    b._audio_session = None
    vol = b.audio_set_volume(50)
    assert vol["ok"] is False
    assert vol["code"] == "session_unavailable"
    assert vol["verified"] is False
    mute = b.audio_set_mute(True)
    assert mute["ok"] is False
    assert mute["code"] == "session_unavailable"


def test_service_rejects_out_of_range_before_backend(monkeypatch):
    class _B:
        def audio_set_volume(self, percent):
            raise AssertionError("backend must not be called for invalid input")

    monkeypatch.setattr(audio_svc, "get_backend", lambda: _B())
    out = audio_svc.set_volume(150)
    assert out["ok"] is False
    assert out["code"] == "invalid_volume"


def test_service_delegates_valid_to_backend(monkeypatch):
    class _B:
        def audio_set_volume(self, percent):
            return {"ok": True, "volume": percent, "verified": True}

        def audio_set_mute(self, muted):
            return {"ok": True, "muted": muted, "verified": True}

    monkeypatch.setattr(audio_svc, "get_backend", lambda: _B())
    assert audio_svc.set_volume(33)["volume"] == 33
    assert audio_svc.set_mute(False)["muted"] is False


def test_readback_failure_restores():
    # boom on second read (verify), after set succeeds
    s = _MutSession(volume=40, muted=False, boom_read_after=1)
    out = wa.set_volume(session=s, percent=60)
    assert out["ok"] is False
    assert out["code"] == "verify_failed"
    assert out.get("restored") is True
    assert s.volume == 40
