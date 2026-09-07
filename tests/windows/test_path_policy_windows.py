"""Permission/path policy against real Windows path shapes."""
from __future__ import annotations

import sys

import pytest

from windows_os_api.os.filesystem.service import _reject_traversal

pytestmark = [
    pytest.mark.windows,
    pytest.mark.skipif(sys.platform != "win32", reason="requires Windows (win32)"),
]


@pytest.mark.parametrize(
    "bad",
    [
        r"..\..\Windows\System32\config\SAM",
        r"C:\Users\..\..\Windows\System32",
        r"sandbox\..\..\Windows\win.ini",
        r"..\..\..\etc\passwd",
        r"foo\..\bar\..\..\secret",
    ],
)
def test_reject_windows_traversal_shapes(bad):
    with pytest.raises(PermissionError):
        _reject_traversal(bad)


def test_allow_normal_windows_paths():
    # Relative sandbox-style paths without ..
    _reject_traversal(r"sandbox\notes.txt")
    _reject_traversal(r"C:\Users\Public\Documents\readme.txt")
    _reject_traversal(".")
