"""Linux-only fixtures — real LinuxBackend, not FakeBackend."""
from __future__ import annotations

import sys

import pytest

if sys.platform == "win32":
    pytest.skip("LinuxBackend tests require non-Windows", allow_module_level=True)


@pytest.fixture()
def linux_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("WINOS_BACKEND", "linux")
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(tmp_path / "sandbox"))
    from windows_os_api.core.runtime.config import get_settings
    from windows_os_api.backends.factory import reset_backend
    from windows_os_api.backends.linux import LinuxBackend

    get_settings.cache_clear()
    reset_backend()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    b = LinuxBackend(sandbox_root=str(sandbox))
    yield b
    get_settings.cache_clear()
    reset_backend()
