"""Artifact smoke: prove the FROZEN binary actually runs.

The build pipeline produces dist/winos-api(.exe) with PyInstaller, but nothing
has ever executed it. PyInstaller onefile fails at RUNTIME, not at build time:
a missing hidden import (comtypes, uiautomation, pywin32, uvicorn workers)
yields a binary that builds cleanly and dies on launch. This script closes that
gap by running the real artifact:

  1. `<binary> version`      -> exits 0 and prints the version of this source tree
  2. `<binary> serve --port` -> real uvicorn server, polled over real HTTP
  3. GET /v1/health          -> unauthenticated liveness
  4. GET /v1/system          -> authenticated, asserts the expected backend
  5. terminate               -> process really exits, no orphan left behind

Exit code 0 = the artifact is usable. Non-zero = do not ship it.

Deliberately stdlib-only for the HTTP side: this validates the binary, so it
must not depend on the dev environment that produced it.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
SMOKE_API_KEY = "smoke-key-not-a-secret"


class SmokeError(RuntimeError):
    """Artifact failed verification."""


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested in tests/unit/test_artifact_smoke.py)
# ---------------------------------------------------------------------------
def binary_name(platform: str) -> str:
    """Frozen binary filename for a sys.platform value."""
    return "winos-api.exe" if platform.startswith("win") else "winos-api"


def resolve_binary(dist_dir: Path, platform: str) -> Path:
    """Locate the frozen artifact, or fail loudly.

    A missing binary is an error, never a skip: this script exists precisely to
    catch the case where the build claimed success and produced nothing usable.
    """
    candidate = dist_dir / binary_name(platform)
    if not candidate.is_file():
        listing = (
            ", ".join(sorted(p.name for p in dist_dir.iterdir())) if dist_dir.is_dir() else "<no dist/>"
        )
        raise SmokeError(f"artifact not found: {candidate} (dist contains: {listing})")
    return candidate


def verify_version(output: str, expected: str) -> str:
    """Check `<binary> version` stdout against the source tree version."""
    got = output.strip()
    if not got:
        raise SmokeError("`version` printed nothing — binary ran but produced no output")
    if got != expected:
        raise SmokeError(f"version mismatch: binary says {got!r}, source tree says {expected!r}")
    return got


def free_port() -> int:
    """Reserve an ephemeral port, then release it for the child to bind."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_is_open(port: int, host: str = "127.0.0.1") -> bool:
    """True when something accepts connections on the port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def wait_for_port_release(port: int, timeout: float = 20.0) -> bool:
    """True once nothing listens on the port any more.

    Polled rather than sampled once: a socket needs a moment to come down after
    the process is killed, and a single instantaneous check would report a
    phantom orphan.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_is_open(port):
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def http_get(url: str, api_key: str | None = None, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url)  # noqa: S310 - fixed localhost URL
    if api_key:
        req.add_header("X-API-Key", api_key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def wait_for_health(port: int, proc: subprocess.Popen, timeout: float = 90.0) -> dict:
    """Poll /v1/health until the server answers, the process dies, or we time out.

    Dying early is reported with the child's own output: that is the message
    that tells a maintainer which hidden import PyInstaller dropped.
    """
    url = f"http://127.0.0.1:{port}/v1/health"
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = (proc.stdout.read() if proc.stdout else "") or ""
            raise SmokeError(
                f"binary exited with code {proc.returncode} before serving.\n"
                f"--- child output ---\n{out.strip()}\n--- end ---"
            )
        try:
            return http_get(url)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            last_err = exc
            time.sleep(0.5)
    raise SmokeError(f"/v1/health did not answer within {timeout:.0f}s (last error: {last_err})")


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------
def run_version_check(binary: Path, expected: str) -> str:
    proc = subprocess.run(  # noqa: S603 - path built from dist/, not user input
        [str(binary), "version"], capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        raise SmokeError(
            f"`{binary.name} version` exited {proc.returncode}\n"
            f"stdout: {proc.stdout.strip()}\nstderr: {proc.stderr.strip()}"
        )
    return verify_version(proc.stdout, expected)


def run_serve_check(binary: Path, expected_backend: str, expected_version: str) -> None:
    port = free_port()
    env = dict(os.environ)
    env["WINOS_API_KEYS"] = json.dumps([SMOKE_API_KEY])
    env["WINOS_REQUIRE_AUTH"] = "true"
    env["WINOS_BACKEND"] = expected_backend

    proc = subprocess.Popen(  # noqa: S603 - path built from dist/, not user input
        [str(binary), "serve", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        health = wait_for_health(port, proc)
        print(f"  /v1/health -> {health}")
        if health.get("status") != "ok":
            raise SmokeError(f"health status not ok: {health}")
        if health.get("version") != expected_version:
            raise SmokeError(
                f"served version {health.get('version')!r} != source {expected_version!r}"
            )

        system = http_get(f"http://127.0.0.1:{port}/v1/system", api_key=SMOKE_API_KEY)
        print(f"  /v1/system -> backend={system.get('backend')} os={system.get('os')}")
        if system.get("backend") != expected_backend:
            raise SmokeError(
                f"backend mismatch: served {system.get('backend')!r}, expected {expected_backend!r}"
            )

        caps = http_get(f"http://127.0.0.1:{port}/v1/capabilities", api_key=SMOKE_API_KEY)
        if not isinstance(caps, dict) or not caps:
            raise SmokeError(f"/v1/capabilities returned no capability map: {caps}")
        print(f"  /v1/capabilities -> {len(caps)} entries")

        unauth_rejected = False
        try:
            http_get(f"http://127.0.0.1:{port}/v1/system")
        except urllib.error.HTTPError as exc:
            unauth_rejected = exc.code in (401, 403)
        if not unauth_rejected:
            raise SmokeError("/v1/system answered without an API key — auth is not enforced")
        print("  /v1/system without key -> rejected (auth enforced)")
    finally:
        _terminate(proc)

    if not wait_for_port_release(port):
        raise SmokeError(f"port {port} still accepting connections — orphan process left behind")
    print("  teardown -> process tree exited, port released")


def _terminate(proc: subprocess.Popen) -> None:
    """Stop the served binary and everything it spawned.

    On Windows a PyInstaller ONEFILE binary is two processes: the bootloader
    unpacks to a temp dir and launches a second copy of itself, which is the one
    actually holding the socket. TerminateProcess on the bootloader alone leaves
    that child serving — observed on GHA, where the runner had to reap
    `Terminate orphan process: pid (5500) (winos-api)` after this script exited.
    A supervisor stops the tree, so that is what we do here.
    """
    if proc.poll() is not None:
        return
    if sys.platform.startswith("win"):
        subprocess.run(  # noqa: S603,S607 - fixed system tool, pid is ours
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=30,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=15)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the frozen winos-api artifact")
    parser.add_argument("--dist", default=str(DIST), help="directory holding the artifact")
    parser.add_argument(
        "--expect-backend",
        default="linux" if not sys.platform.startswith("win") else "windows",
        help="backend the served binary must report",
    )
    parser.add_argument("--expect-version", default=None, help="override expected version")
    args = parser.parse_args(argv)

    expected_version = args.expect_version
    if expected_version is None:
        from windows_os_api import __version__

        expected_version = __version__

    try:
        binary = resolve_binary(Path(args.dist), sys.platform)
        print(f"artifact: {binary} ({binary.stat().st_size / 1_048_576:.1f} MiB)")

        version = run_version_check(binary, expected_version)
        print(f"  version -> {version}")

        run_serve_check(binary, args.expect_backend, expected_version)
    except SmokeError as exc:
        print(f"\nARTIFACT SMOKE FAILED: {exc}", file=sys.stderr)
        return 1

    print("\nARTIFACT SMOKE OK — frozen binary starts, serves and shuts down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
