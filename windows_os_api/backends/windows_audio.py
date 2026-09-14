"""Windows Core Audio (WASAPI) read path — devices / default / volume / mute (N009).

Mutations (set volume/mute + restore) are **N010** and are not implemented here.

No ``pycaw`` (owner D4-B). Production uses ``comtypes`` (already a windows extra)
to talk to ``IMMDeviceEnumerator`` / ``IAudioEndpointVolume``. Tests inject a
session object so Linux CI never opens COM.

Honest outcomes:
- session/API missing → caller sees ``CAPABILITY_UNAVAILABLE`` (implemented, not
  present on this machine) rather than ``CAPABILITY_NOT_SUPPORTED``;
- enumerator works but no endpoints → ``supported: true`` + empty list;
- default/device missing → ``code=device_absent`` (not a fake null volume).
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
