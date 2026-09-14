"""Windows Core Audio (WASAPI) — devices / volume / mute (N009 read + N010 mutate).

N009: list endpoints, read volume/mute.
N010: gated ``set_volume`` / ``set_mute`` with readback and restore-on-failure.

No ``pycaw`` (owner D4-B). Production uses ``comtypes`` (already a windows extra)
to talk to ``IMMDeviceEnumerator`` / ``IAudioEndpointVolume``. Tests inject a
session object so Linux CI never opens COM.

Honest outcomes:
- session/API missing → ``CAPABILITY_UNAVAILABLE`` / ``session_unavailable``
  (implemented, not present on this machine) rather than ``NOT_SUPPORTED``;
- enumerator works but no endpoints → ``supported: true`` + empty list;
- default/device missing → ``code=device_absent`` (not a fake null volume);
- invalid volume (not int 0–100) rejected without mutating;
- after a failed set/verify, prior volume+mute are restored when possible;
- ambiguous / unverified ≠ success.
"""
from __future__ import annotations

from typing import Any, Protocol

from windows_os_api.os.capability import DiscoveryFailed

# MMDevice / endpoint volume GUIDs (mmdeviceapi.h / endpointvolume.h).
_CLSID_MMDEVICE_ENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
_IID_IMMDEVICE_ENUMERATOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
_IID_IAUDIO_ENDPOINT_VOLUME = "{5CDF2C82-841E-4546-9722-0CF74078229A}"
_IID_IMMDEVICE = "{D666063F-1587-4E43-81F1-B948E807363F}"
_IID_IPROPERTY_STORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"

E_RENDER = 0
E_CAPTURE = 1
E_CONSOLE = 0
DEVICE_STATE_ACTIVE = 0x00000001
STGM_READ = 0

# PKEY_Device_FriendlyName
_PKEY_FRIENDLY_NAME = ("{A45C254E-DF1C-4EFD-8020-67D146A850E0}", 14)


class AudioSessionUnavailable(RuntimeError):
    """WASAPI/COM session cannot be opened on this machine."""


class AudioSession(Protocol):
    def list_endpoints(self) -> list[dict[str, Any]]: ...
    def read_volume(self, device_id: str | None = None) -> dict[str, Any]: ...
    def set_volume(self, percent: int, device_id: str | None = None) -> None: ...
    def set_mute(self, muted: bool, device_id: str | None = None) -> None: ...


def try_open_session() -> AudioSession | None:
    """Best-effort production session. None = unavailable, not unsupported."""
    try:
        return _ComtypesSession()
    except Exception:  # noqa: BLE001
        return None


def list_devices(*, session: AudioSession | None) -> list[dict[str, Any]]:
    if session is None:
        raise AudioSessionUnavailable(
            "WASAPI session is not available on this machine (comtypes/COM)"
        )
    try:
        return list(session.list_endpoints())
    except AudioSessionUnavailable:
        raise
    except Exception as exc:
        raise DiscoveryFailed(f"WASAPI endpoint enumeration failed: {exc}") from exc


def get_volume(*, session: AudioSession | None, device_id: str | None = None) -> dict[str, Any]:
    if session is None:
        raise AudioSessionUnavailable(
            "WASAPI session is not available on this machine (comtypes/COM)"
        )
    try:
        return dict(session.read_volume(device_id))
    except AudioSessionUnavailable:
        raise
    except Exception as exc:
        raise DiscoveryFailed(f"WASAPI volume read failed: {exc}") from exc


def _validate_volume_percent(percent: Any) -> int | dict[str, Any]:
    """Return int 0–100 or an honest invalid envelope (no device touch).

    Rejects bool (subclass of int), floats, strings, and out-of-range values
    without touching the session/device.
    """
    if isinstance(percent, bool) or not isinstance(percent, int):
        return {
            "ok": False,
            "supported": True,
            "volume": None,
            "muted": None,
            "verified": False,
            "code": "invalid_volume",
            "error": f"volume must be an int in 0..100 inclusive, got {percent!r}",
            "backend": "wasapi",
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
            "backend": "wasapi",
        }
    return percent


def _capture_prior(session: AudioSession, device_id: str | None) -> dict[str, Any]:
    prior = dict(session.read_volume(device_id))
    return prior


def _attempt_restore(
    session: AudioSession,
    prior: dict[str, Any],
    device_id: str | None,
) -> bool:
    """Best-effort restore of prior volume+mute. Returns True if both applied."""
    if not prior.get("ok"):
        return False
    vol = prior.get("volume")
    muted = prior.get("muted")
    try:
        if vol is not None:
            session.set_volume(int(vol), device_id)
        if muted is not None:
            session.set_mute(bool(muted), device_id)
        return True
    except Exception:  # noqa: BLE001
        return False


def set_volume(
    *,
    session: AudioSession | None,
    percent: Any,
    device_id: str | None = None,
) -> dict[str, Any]:
    """Set master volume (0–100) with readback; restore prior on failure (N010)."""
    validated = _validate_volume_percent(percent)
    if isinstance(validated, dict):
        return validated
    percent = validated

    if session is None:
        raise AudioSessionUnavailable(
            "WASAPI session is not available on this machine (comtypes/COM)"
        )

    try:
        prior = _capture_prior(session, device_id)
    except AudioSessionUnavailable:
        raise
    except Exception as exc:
        return {
            "ok": False,
            "supported": True,
            "volume": None,
            "muted": None,
            "verified": False,
            "code": "read_failed",
            "error": f"failed to capture prior volume: {exc}",
            "backend": "wasapi",
        }

    if not prior.get("ok"):
        out = {
            "ok": False,
            "supported": True,
            "volume": prior.get("volume"),
            "muted": prior.get("muted"),
            "verified": False,
            "code": prior.get("code") or "device_absent",
            "error": prior.get("error") or "cannot set volume: prior read failed",
            "backend": prior.get("backend") or "wasapi",
        }
        return out

    try:
        session.set_volume(percent, device_id)
    except Exception as exc:  # noqa: BLE001
        # Restore even on set failure (partial COM write / deny after touch).
        restored = _attempt_restore(session, prior, device_id)
        return {
            "ok": False,
            "supported": True,
            "volume": prior.get("volume"),
            "muted": prior.get("muted"),
            "verified": False,
            "restored": restored,
            "code": "set_failed",
            "error": str(exc),
            "backend": "wasapi",
        }

    try:
        after = dict(session.read_volume(device_id))
    except Exception as exc:  # noqa: BLE001
        restored = _attempt_restore(session, prior, device_id)
        return {
            "ok": False,
            "supported": True,
            "volume": None,
            "muted": None,
            "verified": False,
            "restored": restored,
            "code": "verify_failed",
            "error": f"set applied but readback failed: {exc}",
            "backend": "wasapi",
        }

    if not after.get("ok") or after.get("volume") != percent:
        restored = _attempt_restore(session, prior, device_id)
        return {
            "ok": False,
            "supported": True,
            "volume": after.get("volume"),
            "muted": after.get("muted"),
            "verified": False,
            "restored": restored,
            "code": "verify_mismatch",
            "error": (
                f"readback volume {after.get('volume')!r} != requested {percent}"
            ),
            "backend": after.get("backend") or "wasapi",
        }

    return {
        "ok": True,
        "supported": True,
        "volume": after.get("volume"),
        "muted": after.get("muted"),
        "verified": True,
        "device_id": after.get("device_id"),
        "backend": after.get("backend") or "wasapi",
    }


def set_mute(
    *,
    session: AudioSession | None,
    muted: Any,
    device_id: str | None = None,
) -> dict[str, Any]:
    """Set mute with readback; restore prior volume+mute on failure (N010)."""
    if not isinstance(muted, bool):
        return {
            "ok": False,
            "supported": True,
            "volume": None,
            "muted": None,
            "verified": False,
            "code": "invalid_mute",
            "error": f"muted must be bool, got {type(muted).__name__}",
            "backend": "wasapi",
        }

    if session is None:
        raise AudioSessionUnavailable(
            "WASAPI session is not available on this machine (comtypes/COM)"
        )

    try:
        prior = _capture_prior(session, device_id)
    except AudioSessionUnavailable:
        raise
    except Exception as exc:
        return {
            "ok": False,
            "supported": True,
            "volume": None,
            "muted": None,
            "verified": False,
            "code": "read_failed",
            "error": f"failed to capture prior mute: {exc}",
            "backend": "wasapi",
        }

    if not prior.get("ok"):
        return {
            "ok": False,
            "supported": True,
            "volume": prior.get("volume"),
            "muted": prior.get("muted"),
            "verified": False,
            "code": prior.get("code") or "device_absent",
            "error": prior.get("error") or "cannot set mute: prior read failed",
            "backend": prior.get("backend") or "wasapi",
        }

    try:
        session.set_mute(muted, device_id)
    except Exception as exc:  # noqa: BLE001
        restored = _attempt_restore(session, prior, device_id)
        return {
            "ok": False,
            "supported": True,
            "volume": prior.get("volume"),
            "muted": prior.get("muted"),
            "verified": False,
            "restored": restored,
            "code": "set_failed",
            "error": str(exc),
            "backend": "wasapi",
        }

    try:
        after = dict(session.read_volume(device_id))
    except Exception as exc:  # noqa: BLE001
        restored = _attempt_restore(session, prior, device_id)
        return {
            "ok": False,
            "supported": True,
            "volume": None,
            "muted": None,
            "verified": False,
            "restored": restored,
            "code": "verify_failed",
            "error": f"set applied but readback failed: {exc}",
            "backend": "wasapi",
        }

    if not after.get("ok") or bool(after.get("muted")) != muted:
        restored = _attempt_restore(session, prior, device_id)
        return {
            "ok": False,
            "supported": True,
            "volume": after.get("volume"),
            "muted": after.get("muted"),
            "verified": False,
            "restored": restored,
            "code": "verify_mismatch",
            "error": f"readback muted {after.get('muted')!r} != requested {muted}",
            "backend": after.get("backend") or "wasapi",
        }

    return {
        "ok": True,
        "supported": True,
        "volume": after.get("volume"),
        "muted": after.get("muted"),
        "verified": True,
        "device_id": after.get("device_id"),
        "backend": after.get("backend") or "wasapi",
    }


class _FakeLike:
    """Not used in production — documents the session contract for tests."""


class _ComtypesSession:
    """IMMDeviceEnumerator via comtypes. Constructed only on Windows with COM."""

    def __init__(self) -> None:
        import comtypes  # type: ignore
        from comtypes import CLSCTX_ALL, GUID, CoCreateInstance  # type: ignore
        from comtypes.client import CreateObject  # type: ignore

        self._comtypes = comtypes
        self._GUID = GUID
        self._CLSCTX_ALL = CLSCTX_ALL
        enumerator_clsid = GUID(_CLSID_MMDEVICE_ENUMERATOR)
        enumerator_iid = GUID(_IID_IMMDEVICE_ENUMERATOR)
        try:
            self._enumerator = CoCreateInstance(
                enumerator_clsid, interface=None, clsctx=CLSCTX_ALL
            )
            # QueryInterface to IMMDeviceEnumerator if the default iface isn't it.
            try:
                from comtypes import IUnknown  # type: ignore

                self._enumerator = self._enumerator.QueryInterface(
                    _load_immdevice_enumerator(GUID, IUnknown)
                )
            except Exception:  # noqa: BLE001
                # Fall through: some comtypes builds expose the enumerator directly.
                pass
        except Exception:
            # CreateObject with the CLSID string is the more portable form.
            self._enumerator = CreateObject(_CLSID_MMDEVICE_ENUMERATOR)
        if self._enumerator is None:
            raise AudioSessionUnavailable("CoCreateInstance MMDeviceEnumerator returned None")
        # Probe: a working enumerator must answer EnumAudioEndpoints.
        if not hasattr(self._enumerator, "EnumAudioEndpoints") and not hasattr(
            self._enumerator, "GetDefaultAudioEndpoint"
        ):
            raise AudioSessionUnavailable("MMDeviceEnumerator missing expected methods")

    def list_endpoints(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        default_ids = set()
        for flow, kind in ((E_RENDER, "output"), (E_CAPTURE, "input")):
            try:
                default = self._default_id(flow)
            except Exception:  # noqa: BLE001
                default = None
            if default:
                default_ids.add(default)
            try:
                coll = self._enumerator.EnumAudioEndpoints(flow, DEVICE_STATE_ACTIVE)
            except Exception:  # noqa: BLE001
                continue
            count = int(coll.GetCount())
            for i in range(count):
                dev = coll.Item(i)
                dev_id = str(dev.GetId())
                name = _friendly_name(dev) or dev_id
                out.append(
                    {
                        "id": dev_id,
                        "name": name,
                        "type": kind,
                        "default": dev_id == default,
                        "backend": "wasapi",
                    }
                )
        return out

    def read_volume(self, device_id: str | None = None) -> dict[str, Any]:
        dev = None
        if device_id:
            try:
                dev = self._enumerator.GetDevice(device_id)
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "supported": True,
                    "volume": None,
                    "muted": None,
                    "code": "device_absent",
                    "error": f"GetDevice({device_id!r}) failed: {exc}",
                    "backend": "wasapi",
                }
        else:
            try:
                dev = self._enumerator.GetDefaultAudioEndpoint(E_RENDER, E_CONSOLE)
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "supported": True,
                    "volume": None,
                    "muted": None,
                    "code": "device_absent",
                    "error": f"no default render endpoint: {exc}",
                    "backend": "wasapi",
                }
        endpoint = _activate_volume(dev)
        if endpoint is None:
            return {
                "ok": False,
                "supported": True,
                "volume": None,
                "muted": None,
                "code": "device_absent",
                "error": "IAudioEndpointVolume not available on this endpoint",
                "backend": "wasapi",
            }
        scalar = float(endpoint.GetMasterVolumeLevelScalar())
        muted = bool(endpoint.GetMute())
        percent = int(round(max(0.0, min(1.0, scalar)) * 100))
        resolved_id = str(dev.GetId()) if hasattr(dev, "GetId") else device_id
        return {
            "ok": True,
            "supported": True,
            "volume": percent,
            "muted": muted,
            "device_id": resolved_id,
            "device_name": _friendly_name(dev),
            "backend": "wasapi",
            "verified": True,
        }

    def set_volume(self, percent: int, device_id: str | None = None) -> None:
        endpoint = self._endpoint_volume(device_id)
        # IAudioEndpointVolume::SetMasterVolumeLevelScalar(float, GUID*)
        endpoint.SetMasterVolumeLevelScalar(float(percent) / 100.0, None)

    def set_mute(self, muted: bool, device_id: str | None = None) -> None:
        endpoint = self._endpoint_volume(device_id)
        endpoint.SetMute(1 if muted else 0, None)

    def _endpoint_volume(self, device_id: str | None = None) -> Any:
        if device_id:
            try:
                dev = self._enumerator.GetDevice(device_id)
            except Exception as exc:  # noqa: BLE001
                raise AudioSessionUnavailable(
                    f"GetDevice({device_id!r}) failed: {exc}"
                ) from exc
        else:
            try:
                dev = self._enumerator.GetDefaultAudioEndpoint(E_RENDER, E_CONSOLE)
            except Exception as exc:  # noqa: BLE001
                raise AudioSessionUnavailable(
                    f"no default render endpoint: {exc}"
                ) from exc
        endpoint = _activate_volume(dev)
        if endpoint is None:
            raise AudioSessionUnavailable(
                "IAudioEndpointVolume not available on this endpoint"
            )
        return endpoint

    def _default_id(self, flow: int) -> str | None:
        try:
            dev = self._enumerator.GetDefaultAudioEndpoint(flow, E_CONSOLE)
            return str(dev.GetId())
        except Exception:  # noqa: BLE001
            return None


def _load_immdevice_enumerator(GUID, IUnknown):
    """Minimal IMMDeviceEnumerator if the typelib wrapper is not generated."""
    # Best-effort: if comtypes.gen already has it, use that.
    try:
        from comtypes.gen import MMDeviceAPILib  # type: ignore

        return MMDeviceAPILib.IMMDeviceEnumerator
    except Exception:  # noqa: BLE001
        return IUnknown


def _friendly_name(dev: Any) -> str | None:
    try:
        store = dev.OpenPropertyStore(STGM_READ)
        # PROPERTYKEY as two-tuple is what many wrappers accept.
        val = store.GetValue(_PKEY_FRIENDLY_NAME)
        return str(getattr(val, "value", val))
    except Exception:  # noqa: BLE001
        return None


def _activate_volume(dev: Any) -> Any | None:
    try:
        from comtypes import CLSCTX_ALL, GUID  # type: ignore

        iid = GUID(_IID_IAUDIO_ENDPOINT_VOLUME)
        return dev.Activate(iid, CLSCTX_ALL, None)
    except Exception:  # noqa: BLE001
        try:
            return dev.Activate(_IID_IAUDIO_ENDPOINT_VOLUME, 1, None)
        except Exception:  # noqa: BLE001
            return None
