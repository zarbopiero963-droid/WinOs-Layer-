"""WindowsBackend must not import on Linux — guarded."""
import sys
import pytest

def test_windows_backend_raises_off_win32():
    if sys.platform == "win32":
        pytest.skip("on Windows")
    from windows_os_api.backends.windows import WindowsBackend, WindowsBackendUnavailable
    with pytest.raises(WindowsBackendUnavailable):
        WindowsBackend()

def test_factory_uses_fake_on_linux(monkeypatch, tmp_path):
    if sys.platform == "win32":
        pytest.skip("on Windows")
    monkeypatch.setenv("WINOS_BACKEND", "auto")
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(tmp_path))
    from windows_os_api.core.runtime.config import get_settings
    from windows_os_api.backends.factory import reset_backend, get_backend
    get_settings.cache_clear()
    reset_backend()
    b = get_backend()
    assert b.name == "fake"

def test_installer_scripts(tmp_path):
    from windows_os_api.installer.service import generate_install_scripts, service_manifest
    written = generate_install_scripts(tmp_path)
    assert (tmp_path / "install_nssm.bat").exists()
    assert "WindowsOSLayerService" in (tmp_path / "install_nssm.bat").read_text()
    assert service_manifest()["bind"].startswith("127.0.0.1")
