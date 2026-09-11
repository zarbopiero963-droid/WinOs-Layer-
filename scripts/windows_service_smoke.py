#!/usr/bin/env python3
"""Hard smoke for the installed WindowsOSLayerService lifecycle.

This runs the exact batch files shipped to users and verifies effects through
SCM, real loopback HTTP, the process table, the listening port and the durable
runtime shutdown audit event. It must run elevated on a disposable Windows CI
runner; any missing prerequisite is a failure, never a skip.
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

import psutil

SERVICE_NAME = "WindowsOSLayerService"
EXE_NAME = "winos-api.exe"


class ServiceSmokeError(RuntimeError):
    """The installed Windows service failed an observable lifecycle contract."""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_until(predicate, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001 - retained for the final diagnosis
            last_error = exc
        time.sleep(0.5)
    detail = f" (last error: {last_error})" if last_error else ""
    raise ServiceSmokeError(f"{what} not observed within {timeout:.0f}s{detail}")


def service_status() -> str | None:
    try:
        return psutil.win_service_get(SERVICE_NAME).status()
    except psutil.NoSuchProcess:
        return None


def winos_processes() -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            if (proc.info.get("name") or "").lower() == EXE_NAME:
                matches.append(dict(proc.info))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return matches


def run_checked(cmd: list[str], what: str, *, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
        check=False,
    )
    output = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part.strip())
    if proc.returncode != 0:
        raise ServiceSmokeError(
            f"{what} exited {proc.returncode}\ncmd: {' '.join(cmd)}\n{output}"
        )
    return output


def invoke_batch(script: Path, what: str, *, env: dict[str, str] | None = None) -> str:
    if not script.is_file():
        raise ServiceSmokeError(f"{what} script missing: {script}")
    return run_checked(["cmd.exe", "/d", "/c", str(script)], what, env=env)


def http_get(port: int, path: str, api_key: str | None = None) -> dict:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if api_key:
        request.add_header("X-API-Key", api_key)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_health(port: int) -> dict:
    result: dict = {}

    def healthy() -> bool:
        nonlocal result
        status = service_status()
        if status in (None, "stopped"):
            raise ServiceSmokeError(f"service became {status!r} before health was ready")
        result = http_get(port, "/v1/health")
        return result.get("status") == "ok"

    wait_until(healthy, 90, "healthy loopback API")
    return result


def shutdown_events(audit_path: Path) -> int:
    if not audit_path.is_file():
        return 0
    count = 0
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        if line.strip() and json.loads(line).get("action") == "server.shutdown":
            count += 1
    return count


def assert_authenticated_api(port: int, api_key: str) -> None:
    system = http_get(port, "/v1/system", api_key)
    if system.get("backend") != "windows":
        raise ServiceSmokeError(f"service returned the wrong backend: {system}")
    try:
        http_get(port, "/v1/system")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return
        raise ServiceSmokeError(f"unauthenticated request returned HTTP {exc.code}") from exc
    raise ServiceSmokeError("/v1/system answered without an API key")


def assert_stopped_cleanly(port: int, audit_path: Path, expected_events: int) -> None:
    wait_until(lambda: service_status() == "stopped", 45, "SCM STOPPED state")
    wait_until(lambda: not port_is_open(port), 30, "released service port")
    wait_until(lambda: not winos_processes(), 30, "zero winos-api.exe processes")
    wait_until(
        lambda: shutdown_events(audit_path) >= expected_events,
        15,
        "graceful server.shutdown audit event",
    )


def cleanup_failed_run(install_dir: Path) -> None:
    if service_status() is not None:
        subprocess.run(
            ["sc.exe", "stop", SERVICE_NAME],
            capture_output=True,
            timeout=30,
            check=False,
        )
        subprocess.run(
            ["sc.exe", "delete", SERVICE_NAME],
            capture_output=True,
            timeout=30,
            check=False,
        )
    for proc in winos_processes():
        cmdline = " ".join(str(part) for part in (proc.get("cmdline") or []))
        exe = str(proc.get("exe") or "")
        if str(install_dir).lower() in (exe + " " + cmdline).lower():
            subprocess.run(
                ["taskkill.exe", "/F", "/T", "/PID", str(proc["pid"])],
                capture_output=True,
                timeout=30,
                check=False,
            )


def run_lifecycle(install_dir: Path) -> None:
    if not sys.platform.startswith("win"):
        raise ServiceSmokeError("Windows service smoke requires a Windows runner")
    if service_status() is not None:
        raise ServiceSmokeError(f"refusing to replace pre-existing {SERVICE_NAME}")
    existing = winos_processes()
    if existing:
        raise ServiceSmokeError(f"pre-existing {EXE_NAME} processes make the test ambiguous: {existing}")

    key_file = install_dir / "api_key.txt"
    key_lines = key_file.read_text(encoding="utf-8").splitlines()
    if len(key_lines) != 1 or not key_lines[0].strip():
        raise ServiceSmokeError("installed api_key.txt must contain exactly one non-empty line")
    api_key = key_lines[0].strip()
    port = free_port()
    audit_path = install_dir / "logs" / "audit.jsonl"
    install_script = install_dir / "service" / "install_nssm.bat"
    uninstall_script = install_dir / "service" / "uninstall_service.bat"
    env = dict(os.environ)
    env["WINOS_SERVICE_PORT"] = str(port)
    created = False

    try:
        # Preflight proved the name absent, so from this point cleanup owns any
        # partial registration even if the batch command itself returns failure.
        created = True
        output = invoke_batch(install_script, "service install", env=env)
        print(output)
        wait_until(lambda: service_status() == "running", 45, "SCM RUNNING state")
        health = wait_for_health(port)
        assert_authenticated_api(port, api_key)
        wait_until(lambda: bool(winos_processes()), 15, "winos-api.exe process tree")
        print(f"  start -> RUNNING, health={health}, auth enforced, process tree present")

        run_checked(["sc.exe", "stop", SERVICE_NAME], "sc stop")
        assert_stopped_cleanly(port, audit_path, expected_events=1)
        print("  stop -> STOPPED, graceful audit event, zero processes, port released")

        run_checked(["sc.exe", "start", SERVICE_NAME], "sc start")
        wait_until(lambda: service_status() == "running", 45, "SCM RUNNING after restart")
        wait_for_health(port)
        assert_authenticated_api(port, api_key)
        print("  restart -> RUNNING, authenticated API recovered")

        output = invoke_batch(uninstall_script, "service uninstall", env=env)
        print(output)
        wait_until(lambda: service_status() is None, 45, "service removal")
        wait_until(lambda: not port_is_open(port), 30, "released port after uninstall")
        wait_until(lambda: not winos_processes(), 30, "zero processes after uninstall")
        wait_until(lambda: shutdown_events(audit_path) >= 2, 15, "second graceful shutdown event")
        print("  uninstall -> service absent, graceful stop, zero residue")
        created = False
    finally:
        if created:
            cleanup_failed_run(install_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hard-test the installed Windows service")
    parser.add_argument("--install-dir", required=True)
    args = parser.parse_args(argv)
    try:
        run_lifecycle(Path(args.install_dir).resolve())
    except (OSError, UnicodeError, json.JSONDecodeError, ServiceSmokeError) as exc:
        print(f"\nWINDOWS SERVICE SMOKE FAILED: {exc}", file=sys.stderr)
        return 1
    print("\nWINDOWS SERVICE SMOKE OK — start, stop, restart and uninstall are clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
