"""Linux-only fixtures — real LinuxBackend, not FakeBackend.

Also home to the real-X-window harness: a window manager and an actual `xterm`
on the live display. Any test that needs a window to act on takes `probe_window`
instead of hoping some application happens to be installed on the runner.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

import pytest

if sys.platform == "win32":
    pytest.skip("LinuxBackend tests require non-Windows", allow_module_level=True)

# xdotool acts, wmctrl handles the EWMH maximize atoms, xprop (x11-utils) reads
# the state back, openbox honours the hints, xterm is the window itself.
WM_TOOLS = ("xdotool", "wmctrl", "xprop", "xterm", "openbox")


def x_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":2")
    return env


def missing_wm_tools() -> list[str]:
    return [t for t in WM_TOOLS if not shutil.which(t)]


def _wm_is_running() -> bool:
    try:
        r = subprocess.run(  # noqa: S603
            ["wmctrl", "-m"], capture_output=True, text=True, timeout=5,
            check=False, env=x_env(),
        )
    except Exception:  # noqa: BLE001
        return False
    return r.returncode == 0


def require_wm_tools() -> None:
    """Skip when the X tooling is absent — but fail when CI says it should be there.

    A silently skipped suite reads exactly like a passing one. `WINOS_REQUIRE_WM=1`
    is set in the workflow step that installs these tools, so a skip there would
    mean the install broke and the coverage quietly went away.
    """
    missing = missing_wm_tools()
    if not missing:
        return
    message = f"window manager tools not installed: {', '.join(missing)}"
    if os.environ.get("WINOS_REQUIRE_WM") == "1":
        pytest.fail(message + " (WINOS_REQUIRE_WM=1)")
    pytest.skip(message)


@dataclass(frozen=True)
class ProbeWindow:
    hwnd: int
    title: str


@pytest.fixture(scope="module")
def window_manager():
    """A running window manager on the current display.

    minimize and maximize are EWMH hints: with nothing to honour them,
    `_NET_WM_STATE` never appears and a test would be measuring nothing.
    """
    require_wm_tools()
    proc = None
    if not _wm_is_running():
        proc = subprocess.Popen(  # noqa: S603
            ["openbox"],
            env=x_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(50):
            if _wm_is_running():
                break
            time.sleep(0.2)
    if not _wm_is_running():
        pytest.fail("openbox did not come up on this display")
    yield
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def probe_window(window_manager):
    """A real, mapped X window — torn down whatever the test does to it."""
    title = f"winos-wm-probe-{os.getpid()}-{int(time.time() * 1000) % 100000}"
    proc = subprocess.Popen(  # noqa: S603
        # `-e sleep 600` keeps the shell out of it: an interactive shell rewrites
        # the title through escape sequences and the search below stops matching.
        ["xterm", "-title", title, "-geometry", "40x10+100+100", "-e", "sleep", "600"],
        env=x_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    found = None
    for _ in range(75):
        r = subprocess.run(  # noqa: S603
            ["xdotool", "search", "--name", title],
            capture_output=True, text=True, timeout=5, check=False, env=x_env(),
        )
        ids = [line for line in r.stdout.split() if line.isdigit()]
        if ids:
            found = int(ids[0])
            break
        time.sleep(0.2)
    if found is None:
        proc.kill()
        pytest.fail(f"xterm window {title!r} never appeared on the display")

    # Existing in X is not the same as being MANAGED. `xdotool search` walks the
    # X tree directly and sees the window as soon as it is created; `wmctrl -l`
    # reads `_NET_CLIENT_LIST`, which the window manager publishes a moment
    # later — measured at ~65ms here, and evidently longer on a CI runner, where
    # a fixed `sleep(0.5)` was not enough and `list_windows()` came back empty.
    #
    # So wait for the condition, not for a duration. Anything that reads the WM's
    # view of the world — list_windows, and the maximize atoms — needs the window
    # to be in it.
    managed = False
    for _ in range(100):
        r = subprocess.run(  # noqa: S603
            ["wmctrl", "-l"], capture_output=True, text=True, timeout=5,
            check=False, env=x_env(),
        )
        if title in (r.stdout or ""):
            managed = True
            break
        time.sleep(0.1)
    if not managed:
        proc.kill()
        pytest.fail(f"window manager never took {title!r} into _NET_CLIENT_LIST")

    yield ProbeWindow(hwnd=found, title=title)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def hwnd(probe_window):
    return probe_window.hwnd


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
