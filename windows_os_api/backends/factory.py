"""Backend factory — auto selects Windows or Fake."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from windows_os_api.core.runtime.config import get_settings


@lru_cache
def get_backend() -> Any:
    settings = get_settings()
    kind = settings.resolve_backend()
    sandbox = settings.sandbox_root
    if kind == "windows":
        from windows_os_api.backends.windows import WindowsBackend, WindowsBackendUnavailable

        try:
            return WindowsBackend(sandbox_root=sandbox)
        except WindowsBackendUnavailable:
            from windows_os_api.backends.fake import FakeBackend

            return FakeBackend(sandbox_root=sandbox)
    from windows_os_api.backends.fake import FakeBackend

    return FakeBackend(sandbox_root=sandbox)


def reset_backend() -> None:
    get_backend.cache_clear()
