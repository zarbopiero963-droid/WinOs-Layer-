"""Windows hard-test fixtures — skip module collection helpers."""
from __future__ import annotations

import sys

import pytest

windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="WindowsBackend hard tests require win32 (skipped on Linux)",
)


@pytest.fixture
def win_backend(tmp_path):
    """Instantiate real WindowsBackend (fails on Linux before skip if mis-marked)."""
    if sys.platform != "win32":
        pytest.skip("WindowsBackend requires win32")
    from windows_os_api.backends.windows import WindowsBackend

    return WindowsBackend(sandbox_root=str(tmp_path / "sandbox"))
