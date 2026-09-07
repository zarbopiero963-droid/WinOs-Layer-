"""Backend factory — auto selects Windows / Linux / Fake."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from windows_os_api.core.runtime.config import get_settings


class BackendUnavailable(RuntimeError):
    """Raised when an explicitly requested backend cannot run on this host."""


@lru_cache
def get_backend() -> Any:
    settings = get_settings()
    kind = settings.resolve_backend()
    sandbox = settings.sandbox_root
    allow_paths = list(getattr(settings, "fs_allow_paths", []) or [])
    allow_fake = bool(getattr(settings, "allow_fake_fallback", False))

    if kind == "windows":
        from windows_os_api.backends.windows import WindowsBackend, WindowsBackendUnavailable

        try:
            return WindowsBackend(sandbox_root=sandbox)
        except WindowsBackendUnavailable as e:
            if allow_fake:
                from windows_os_api.backends.fake import FakeBackend

                return FakeBackend(sandbox_root=sandbox)
            raise BackendUnavailable(
                "WindowsBackend requires win32. "
                "Use WINOS_BACKEND=linux (or auto) on Linux, "
                "WINOS_BACKEND=fake for CRM fixtures, "
                "or set WINOS_ALLOW_FAKE_FALLBACK=true."
            ) from e

    if kind == "linux":
        from windows_os_api.backends.linux import LinuxBackend, LinuxBackendUnavailable

        try:
            return LinuxBackend(sandbox_root=sandbox, allow_paths=allow_paths)
        except LinuxBackendUnavailable as e:
            if allow_fake:
                from windows_os_api.backends.fake import FakeBackend

                return FakeBackend(sandbox_root=sandbox)
            raise BackendUnavailable(
                "LinuxBackend is not available on this platform. "
                "Use WINOS_BACKEND=windows on Win32 or WINOS_BACKEND=fake."
            ) from e

    if kind == "fake":
        from windows_os_api.backends.fake import FakeBackend

        return FakeBackend(sandbox_root=sandbox)

    raise BackendUnavailable(f"Unknown backend kind: {kind}")


def reset_backend() -> None:
    get_backend.cache_clear()
