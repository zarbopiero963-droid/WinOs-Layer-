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

# The input tests aim at `event_recorder.center` and offset up to 60px from it
# (the drag in `test_drag_presses_moves_and_releases` is the widest), so the
# recorder window must be able to contain those offsets or the event lands on
# the root window and the recorder truthfully reports having seen nothing.
MAX_AIM_OFFSET = 60
MIN_RECORDER_SIDE = 2 * MAX_AIM_OFFSET + 30  # 150: the offsets, plus margin


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


def xdo(args: list[str], timeout: float = 10.0):
    """Run `xdotool`, bounded. Returns the result, or `None` when the call hung.

    `--sync` blocks until the window manager has acknowledged the change. That is
    what makes it useful, and it is also what makes it a hang: the window manager
    has to answer the ConfigureRequest, and if it is busy it simply does not.
    `subprocess.run(..., timeout=...)` then RAISES out of the middle of a fixture,
    and one stalled `windowsize` takes the whole job down — that is what happened
    on CI (`test-linux`, run 34276587508):

        subprocess.TimeoutExpired: Command '['xdotool', 'windowsize', '--sync',
        '4194305', '900', '700']' timed out after 10 seconds

    Reproduced deterministically with `SIGSTOP` on openbox: `windowmove --sync`
    and `windowsize --sync` both sit there for the full timeout, while a plain
    `getwindowgeometry` still answers in 0.00s. A window manager that stalls for
    a moment — a loaded runner does exactly that — is not a reason to lose a
    test run.

    So the wait is bounded here, a timeout comes back as `None` instead of being
    raised, and the caller establishes what actually happened by MEASURING the
    window rather than by trusting that the request was carried out.
    """
    try:
        return subprocess.run(  # noqa: S603
            ["xdotool", *args],
            capture_output=True, text=True, timeout=timeout, check=False, env=x_env(),
        )
    except subprocess.TimeoutExpired:
        return None


def window_geometry(window_id: int) -> dict[str, int]:
    """The rectangle X reports for `window_id`, or `{}` when it cannot be read.

    Empty for a window that does not exist, for one that has just been
    destroyed, and for a call that hung — three ways of not knowing, none of
    which should reach a test as an exception from inside a fixture.
    """
    result = xdo(["getwindowgeometry", "--shell", str(window_id)], timeout=5)
    if result is None:
        return {}
    rect: dict[str, int] = {}
    for line in (result.stdout or "").splitlines():
        key, _, raw = line.partition("=")
        key = key.strip().lower()
        if key in ("x", "y", "width", "height"):
            try:
                rect[key] = int(raw.strip())
            except ValueError:
                continue
    return rect if len(rect) == 4 else {}


def wait_until_managed(title: str, timeout: float = 20.0) -> bool:
    """Wait for the window manager to take `title` into `_NET_CLIENT_LIST`.

    Existing in X is not the same as being MANAGED. `xdotool search` walks the
    X tree directly and sees the window as soon as it is created; `wmctrl -l`
    reads `_NET_CLIENT_LIST`, which the window manager publishes a moment
    later — measured at ~65ms here, and evidently longer on a CI runner, where
    a fixed `sleep(0.5)` was not enough and `list_windows()` came back empty.

    So wait for the condition, not for a duration. Anything that reads the WM's
    view of the world — list_windows, and the maximize atoms — needs the window
    to be in it, and so does anything that ASKS the window manager to move or
    resize the window: a request aimed at a window it has not adopted yet is a
    request nobody answers.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = subprocess.run(  # noqa: S603
                ["wmctrl", "-l"], capture_output=True, text=True, timeout=5,
                check=False, env=x_env(),
            )
        except subprocess.TimeoutExpired:
            # Same reasoning as `xdo`: a probe that does not answer is a "not
            # yet", not an exception raised from inside a fixture.
            time.sleep(0.1)
            continue
        if title in (result.stdout or ""):
            return True
        time.sleep(0.1)
    return False


def wait_for_focus(window_id: int, timeout: float = 5.0) -> bool:
    """Wait until the X input focus is on `window_id`.

    Focus is not decoration for the recorder: keystrokes go to whichever window
    holds it, so a recorder without focus records nothing and the test blames
    the input layer for a delivery that happened somewhere else.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = xdo(["getwindowfocus"], timeout=5)
        if result is not None:
            out = (result.stdout or "").strip()
            if out.isdigit() and int(out) == window_id:
                return True
        time.sleep(0.05)
    return False


@dataclass(frozen=True)
class ProbeWindow:
    hwnd: int
    title: str


@pytest.fixture(scope="session")
def window_manager():
    """A running window manager on the current display.

    minimize and maximize are EWMH hints: with nothing to honour them,
    `_NET_WM_STATE` never appears and a test would be measuring nothing.

    Session-scoped, not module-scoped. At module scope this fixture terminated
    openbox at the end of every module and the next module started a fresh one,
    so a run went through the teardown/startup window once per module — and in
    that window the window manager is present but not answering, which is
    exactly the state in which an `xdotool --sync` waits for a reply that never
    comes (see `xdo`). One window manager for the whole session removes the
    churn; `xdo` handles the stall if it happens anyway.
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
    yield from _spawn_probe_window("winos-wm-probe")


def _spawn_probe_window(prefix: str):
    """Open one real xterm and yield it, cleaning up however the test ends."""
    title = f"{prefix}-{os.getpid()}-{int(time.time() * 1000000) % 1000000}"
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

    # Being in the X tree is not being MANAGED — the wait, and why, are in
    # `wait_until_managed`.
    if not wait_until_managed(title):
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


@pytest.fixture
def second_probe_window(window_manager):
    """A SECOND real window.

    With only one window on the display, "this window has focus" is a claim no
    implementation can get wrong — the one candidate is always the answer. Two
    windows make the assertion mean something.
    """
    yield from _spawn_probe_window("winos-wm-probe2")


class EventRecorder:
    """Reads back the X events the server actually delivered, via `xev`.

    Input has no readback the way window geometry does: once an event is handed
    to the X server it belongs to whatever window has focus, and nothing reports
    what that window did with it. `xev` prints every event it receives, so a
    test can assert on delivery instead of on a tool's exit code.

    Reads from an offset taken when the fixture yields, so the noise `xev` emits
    while its own window is being created and mapped is not counted as input.
    """

    def __init__(self, path, start_offset: int):
        self.path = path
        self.start_offset = start_offset

    def text(self) -> str:
        with open(self.path, encoding="utf-8", errors="replace") as fh:
            fh.seek(self.start_offset)
            return fh.read()

    def count(self, pattern: str) -> int:
        import re

        # MULTILINE, because the patterns anchor on `^` to match an event line
        # like "KeyPress event, serial 44, ...". Without it `^` only matches the
        # very start of the capture, and every count comes back 0 while the
        # events are sitting right there in the text.
        return len(re.findall(pattern, self.text(), re.MULTILINE))

    def wait_for(self, pattern: str, at_least: int = 1, timeout: float = 5.0) -> int:
        """Poll until `pattern` has appeared `at_least` times. Returns the count.

        X delivery is asynchronous, so an immediate read can miss an event that
        is genuinely on its way. This waits for the condition rather than
        sleeping a guessed amount and hoping.
        """
        deadline = time.time() + timeout
        seen = 0
        while time.time() < deadline:
            seen = self.count(pattern)
            if seen >= at_least:
                return seen
            time.sleep(0.05)
        return seen


@pytest.fixture
def event_recorder(window_manager, tmp_path):
    """A focused `xev` window that records what the X server delivers to it."""
    require_wm_tools()
    if not shutil.which("xev"):
        message = "xev not installed (package x11-utils)"
        if os.environ.get("WINOS_REQUIRE_WM") == "1":
            pytest.fail(message + " (WINOS_REQUIRE_WM=1)")
        pytest.skip(message)

    title = f"winos-input-probe-{os.getpid()}-{int(time.time() * 1000) % 100000}"
    log = tmp_path / "xev.log"
    with open(log, "wb") as sink:
        proc = subprocess.Popen(  # noqa: S603
            ["xev", "-name", title],
            env=x_env(),
            stdout=sink,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        found = None
        for _ in range(100):
            r = xdo(["search", "--name", title], timeout=5)
            ids = [line for line in (r.stdout if r else "").split() if line.isdigit()]
            if ids:
                found = int(ids[0])
                break
            time.sleep(0.1)
        if found is None:
            proc.kill()
            pytest.fail(f"xev window {title!r} never appeared")

        # Openbox normally adopts xev.  A loaded runner can leave it mapped but
        # absent from _NET_CLIENT_LIST; event delivery is still testable there
        # because X exposes the real geometry and direct input focus.  Only WM
        # operations are conditional -- the event assertions remain identical.
        managed = wait_until_managed(title)

        # Put it somewhere known and make it big. xev's default window is
        # 178x178 wherever the WM decides to place it — measured at (552,450) —
        # so a click at an arbitrary coordinate lands on the root window
        # instead, and the recorder sees nothing while the event was delivered
        # perfectly well somewhere else.
        #
        # These are REQUESTS, not facts. `xdo` bounds each one so a
        # `--sync` that never gets its answer cannot raise out of the fixture,
        # and what actually happened is established below by measuring.
        requests = (
            (
                ["windowmove", "--sync", str(found), "40", "40"],
                ["windowsize", "--sync", str(found), "900", "700"],
                ["windowactivate", "--sync", str(found)],
            )
            if managed
            else (["windowfocus", "--sync", str(found)],)
        )
        for args in requests:
            xdo(args)

        # Wait for a usable rectangle rather than sleeping a guessed amount. If
        # the window manager never honours the resize the window keeps xev's own
        # 178x178, which the tests can still aim inside — the requirement is not
        # that the resize worked, it is that the rectangle the tests aim at was
        # MEASURED and is big enough to contain the offsets they use.
        rect: dict[str, int] = {}
        deadline = time.time() + 10
        while time.time() < deadline:
            rect = window_geometry(found)
            if rect and min(rect["width"], rect["height"]) >= MIN_RECORDER_SIDE:
                break
            time.sleep(0.1)
        if not rect:
            proc.kill()
            pytest.fail(f"could not read the geometry of the xev window {title!r}")
        if min(rect["width"], rect["height"]) < MIN_RECORDER_SIDE:
            proc.kill()
            pytest.fail(
                f"the xev window {title!r} is {rect['width']}x{rect['height']}: too small "
                f"for a test to aim {MAX_AIM_OFFSET}px off centre and still land inside it"
            )

        # `windowactivate` above may have hung or been ignored. Read the focus
        # back instead of trusting it, and ask once more if it went elsewhere:
        # without focus the recorder receives no keystroke, and the test would
        # report that the input layer delivered nothing.
        if not wait_for_focus(found, timeout=3.0):
            focus_command = "windowactivate" if managed else "windowfocus"
            xdo([focus_command, str(found)], timeout=5)
            if not wait_for_focus(found, timeout=5.0):
                proc.kill()
                pytest.fail(f"the xev window {title!r} never took the input focus")

        time.sleep(0.6)  # let the map/expose/focus/configure burst finish

        recorder = EventRecorder(log, log.stat().st_size)
        recorder.hwnd = found
        recorder.title = title
        recorder.rect = rect
        # Where a test should aim so the event lands INSIDE the recorder.
        recorder.center = (rect["x"] + rect["width"] // 2,
                           rect["y"] + rect["height"] // 2)
        try:
            yield recorder
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


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
