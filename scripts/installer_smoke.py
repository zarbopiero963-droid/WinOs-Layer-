"""Installer smoke: prove WinOsApi-Setup actually installs, runs and uninstalls.

`WinOsApi-Setup-<version>.exe` has been produced by CI and shipped without ever
being installed by anyone. This drives its real lifecycle on Windows:

  1. silent install                 -> the layout the .iss promises appears
  2. the INSTALLED binary runs      -> delegated to artifact_smoke, so what the
                                       user actually receives is what gets tested
  3. real Windows service lifecycle -> start, stop, restart, remove; no orphan
  4. silent uninstall               -> no binary and no install dir left behind

Each step asserts the EFFECT, not the exit of the installer process. Observed
on GHA: Setup.exe completes the install ("Installation process succeeded" in
its own log, every file on disk) and then never terminates. Waiting on process
exit therefore hangs on work that is already done. That non-exit is a real
defect of the installer, reported loudly here and tracked in issue #6 — it is
not swallowed, it is simply not allowed to block the verification.

The installer does not register a service automatically. The smoke invokes the
same optional ``service/install_nssm.bat`` shortcut a user would run, then the
matching uninstall script, before uninstalling the product files.

Windows-only by nature. The pure helpers are unit-tested cross-platform in
tests/unit/test_installer_smoke.py.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "installer" / "output"
SETUP_GLOB = "WinOsApi-Setup-*.exe"
EXE_NAME = "winos-api.exe"
# Inno writes its uninstaller as unins000.exe in the install directory.
UNINSTALLER = "unins000.exe"
# Files the .iss promises: [Files] + the api_key.txt written in [Code] ssPostInstall.
EXPECTED_AFTER_INSTALL = (EXE_NAME, "api_key.txt", "service")


class InstallerSmokeError(RuntimeError):
    """Installer failed verification."""


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------
def find_setup(output_dir: Path) -> Path:
    """Locate the Setup executable, or fail loudly."""
    if not output_dir.is_dir():
        raise InstallerSmokeError(f"installer output dir missing: {output_dir}")
    matches = sorted(output_dir.glob(SETUP_GLOB))
    if not matches:
        listing = ", ".join(sorted(p.name for p in output_dir.iterdir())) or "<empty>"
        raise InstallerSmokeError(
            f"no {SETUP_GLOB} in {output_dir} (contains: {listing})"
        )
    return matches[-1]


def uninstaller_path(install_dir: Path) -> Path:
    return install_dir / UNINSTALLER


def verify_installed_layout(install_dir: Path) -> None:
    """Every file the .iss promises must be on disk after a silent install."""
    if not install_dir.is_dir():
        raise InstallerSmokeError(f"install dir was not created: {install_dir}")
    missing = [name for name in EXPECTED_AFTER_INSTALL if not (install_dir / name).exists()]
    if missing:
        present = ", ".join(sorted(p.name for p in install_dir.iterdir())) or "<empty>"
        raise InstallerSmokeError(
            f"install incomplete, missing {missing} in {install_dir} (present: {present})"
        )
    key_file = install_dir / "api_key.txt"
    if not key_file.read_text(encoding="utf-8", errors="replace").strip():
        raise InstallerSmokeError("api_key.txt was created but is empty")


def verify_removed(install_dir: Path) -> None:
    """After uninstall the binary must be gone — a leftover EXE is a failed uninstall."""
    leftover = install_dir / EXE_NAME
    if leftover.exists():
        raise InstallerSmokeError(f"uninstall left the binary behind: {leftover}")
    if install_dir.is_dir():
        remaining = sorted(p.name for p in install_dir.iterdir())
        if remaining:
            raise InstallerSmokeError(
                f"uninstall left files in {install_dir}: {', '.join(remaining)}"
            )


# ---------------------------------------------------------------------------
# Windows lifecycle
# ---------------------------------------------------------------------------
def _log_tail(log: Path | None) -> str:
    """Inno's log, or an explicit statement that there isn't one.

    Inno writes almost nothing to stdout, so this log is the only account of what
    happened — and this code runs where nobody can reproduce the problem by hand.
    An ABSENT log is itself a diagnosis: the installer never got far enough to
    open it, which points at the process never really starting (elevation).
    """
    if log is None:
        return ""
    if not log.is_file():
        return f"\n--- {log.name}: not created — the installer never started writing it ---"
    body = log.read_text(errors="replace").strip()
    return f"\n--- {log.name} ---\n{body}\n--- end ---"


def _run(
    cmd: list[str], what: str, timeout: int = 180, log: Path | None = None, capture: bool = True
) -> None:
    """Run a command that is expected to exit on its own.

    Used only for the artifact smoke against the installed binary. The Inno
    commands do NOT go through here: they do not reliably exit, so they are
    driven by silent_install_observed / silent_uninstall_observed, which assert
    the effect instead.
    """
    try:
        proc = subprocess.run(  # noqa: S603
            cmd, capture_output=capture, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise InstallerSmokeError(
            f"{what} did not finish within {timeout}s.\n"
            f"cmd: {' '.join(cmd)}\n"
            f"If the log below says the installation succeeded, the command itself "
            f"completed and it is the wait that hung — check for a surviving helper "
            f"process holding the pipes open." + _log_tail(log)
        ) from exc
    if proc.returncode != 0:
        streams = ""
        if capture:
            streams = f"\nstdout: {proc.stdout.strip()}\nstderr: {proc.stderr.strip()}"
        raise InstallerSmokeError(
            f"{what} exited {proc.returncode}\ncmd: {' '.join(cmd)}{streams}" + _log_tail(log)
        )


def wait_for(check, timeout: float, what: str) -> None:
    """Poll a verification until it passes, or re-raise its last failure.

    The installer does its work in a helper process, so the effect appears while
    the launcher is still around. Polling keeps the assertion strict — the
    condition must still become true, or this raises — while tolerating that the
    work is finishing somewhere we are not watching.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            check()
            return
        except InstallerSmokeError as exc:
            if time.monotonic() >= deadline:
                raise InstallerSmokeError(
                    f"{what} not satisfied within {timeout:.0f}s: {exc}"
                ) from exc
            time.sleep(1.0)


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform.startswith("win"):
        subprocess.run(  # noqa: S603,S607 - fixed system tool, pid is ours
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=60
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


def wait_or_kill_tree(proc: subprocess.Popen, grace: float = 30.0) -> bool:
    """Give the process time to exit on its own; kill its tree if it will not.

    Returns True if it exited by itself. False means it had to be killed — which
    is reported, never swallowed.
    """
    try:
        proc.wait(timeout=grace)
        return True
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        return False


def silent_install_observed(setup: Path, install_dir: Path, log: Path) -> bool:
    """Install, then wait for the RESULT rather than for the process to exit.

    Observed on GHA across three runs: Setup.exe completes the installation —
    the Inno log records "Installation process succeeded" and every file lands
    on disk — and then never terminates; the runner reaps its
    WinOsApi-Setup-<v>.tmp helper as an orphan afterwards. Waiting on process
    exit therefore hangs on a job that has actually finished its work.

    So the assertion moves to what we actually care about and can trust: the
    layout the .iss promises must appear. It is not relaxed — it must still
    become true, or this fails. The installer's failure to exit is returned to
    the caller and reported loudly, because an unattended deploy that waits on
    Setup.exe would hang on it (tracked in issue #6).
    """
    proc = subprocess.Popen(  # noqa: S603
        [
            str(setup),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/NOCANCEL",
            f"/DIR={install_dir}",
            f"/LOG={log}",
        ]
    )
    try:
        wait_for(lambda: verify_installed_layout(install_dir), 180, "installed layout")
    except InstallerSmokeError as exc:
        _kill_tree(proc)
        raise InstallerSmokeError(f"{exc}{_log_tail(log)}") from exc
    return wait_or_kill_tree(proc)


def silent_uninstall_observed(install_dir: Path) -> bool:
    """Uninstall, then wait for the removal rather than for the process to exit.

    Inno's uninstaller relaunches itself from a temp copy, so it has the same
    non-exiting behaviour as Setup.exe. Same approach: assert the effect.
    """
    uninstaller = uninstaller_path(install_dir)
    if not uninstaller.is_file():
        raise InstallerSmokeError(f"uninstaller missing: {uninstaller}")
    proc = subprocess.Popen(  # noqa: S603
        [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
    )
    try:
        wait_for(lambda: verify_removed(install_dir), 180, "removal after uninstall")
    except InstallerSmokeError:
        _kill_tree(proc)
        raise
    return wait_or_kill_tree(proc)


def run_installed_binary(install_dir: Path) -> None:
    """Reuse artifact_smoke against the INSTALLED copy.

    The point of the whole exercise: verify what the user receives after running
    the installer, not only what the build produced in dist/.
    """
    smoke = ROOT / "scripts" / "artifact_smoke.py"
    _run(
        [
            sys.executable,
            str(smoke),
            "--dist",
            str(install_dir),
            "--expect-backend",
            "windows",
        ],
        "artifact smoke on the installed binary",
    )


def run_windows_service_lifecycle(install_dir: Path) -> None:
    """Drive the shipped service scripts against the installed frozen EXE."""
    smoke = ROOT / "scripts" / "windows_service_smoke.py"
    _run(
        [sys.executable, str(smoke), "--install-dir", str(install_dir)],
        "Windows service lifecycle smoke",
        capture=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the Windows installer lifecycle")
    parser.add_argument("--output-dir", default=str(OUTPUT))
    parser.add_argument(
        "--install-dir",
        # Deliberately NOT under Program Files: that path contains a space, and
        # Inno's /DIR= is fragile about quoted paths. A space-free scratch dir
        # removes a failure mode that has nothing to do with what we're testing,
        # and leaves any real installation on the machine untouched.
        default=str(Path(os.environ.get("RUNNER_TEMP", r"C:\\")) / "WinOsApiSmoke"),
        help="scratch install target (avoid paths with spaces)",
    )
    args = parser.parse_args(argv)

    if not sys.platform.startswith("win"):
        print("installer smoke is Windows-only (Inno Setup)", file=sys.stderr)
        return 1

    install_dir = Path(args.install_dir)
    # Beside the install dir, not in tempfile.gettempdir(): on Windows the temp
    # path can contain spaces, and Inno's /LOG= has the same quoting fragility
    # as /DIR=. Same reason the install target avoids Program Files.
    log = install_dir.parent / "winos-installer-smoke.log"
    try:
        setup = find_setup(Path(args.output_dir))
        print(f"installer: {setup} ({setup.stat().st_size / 1_048_576:.1f} MiB)")

        exited = silent_install_observed(setup, install_dir, log)
        print(f"  install -> {install_dir}")
        print(f"  layout  -> {', '.join(EXPECTED_AFTER_INSTALL)} present, api_key.txt non-empty")
        if not exited:
            print(
                "  WARNING: Setup.exe completed the installation but never exited; "
                "its process tree was killed. An unattended deploy that waits on "
                "Setup.exe would hang here. Tracked in issue #6."
            )

        run_installed_binary(install_dir)
        print("  installed binary -> serves and shuts down")

        run_windows_service_lifecycle(install_dir)
        print("  Windows service -> start, stop, restart and removal verified")

        exited = silent_uninstall_observed(install_dir)
        print("  uninstall -> binary and install dir removed")
        if not exited:
            print("  WARNING: the uninstaller never exited either; process tree killed.")
    except InstallerSmokeError as exc:
        print(f"\nINSTALLER SMOKE FAILED: {exc}", file=sys.stderr)
        return 1

    print("\nINSTALLER SMOKE OK — installs, runs, uninstalls clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
