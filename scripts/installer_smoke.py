"""Installer smoke: prove WinOsApi-Setup actually installs, runs and uninstalls.

`WinOsApi-Setup-<version>.exe` has been produced by CI and shipped without ever
being installed by anyone. This drives its real lifecycle on Windows:

  1. silent install                 -> exits 0
  2. installed layout               -> the files the .iss promises are on disk
  3. the INSTALLED binary runs      -> delegated to artifact_smoke, so what the
                                       user actually receives is what gets tested
  4. silent uninstall               -> exits 0
  5. removal                        -> no binary and no install dir left behind

Scope note, taken from installer/inno/winos-api.iss rather than assumed:
the installer does NOT register the Windows service. Service installation is a
separate manual step (a Start Menu shortcut to service/install_nssm.bat), so
asserting a registered service here would be testing something the installer
never claimed to do. Service lifecycle belongs to the PR39 work.

Windows-only by nature. The pure helpers are unit-tested cross-platform in
tests/unit/test_installer_smoke.py.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
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


def _run(cmd: list[str], what: str, timeout: int = 180, log: Path | None = None) -> None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603
    except subprocess.TimeoutExpired as exc:
        # A hang here is not an infrastructure hiccup: it means the installer is
        # waiting for input nobody can give. Fail fast and say so, instead of
        # burning runner minutes on a silent stall — and never let the raw
        # TimeoutExpired escape, which would lose the log below.
        raise InstallerSmokeError(
            f"{what} did not finish within {timeout}s — it is waiting for something.\n"
            f"cmd: {' '.join(cmd)}\n"
            f"Most likely an elevation prompt: the .iss sets PrivilegesRequired=admin, "
            f"and /SUPPRESSMSGBOXES suppresses Inno's own message boxes but NOT a "
            f"Windows UAC consent dialog." + _log_tail(log)
        ) from exc
    if proc.returncode != 0:
        raise InstallerSmokeError(
            f"{what} exited {proc.returncode}\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stdout: {proc.stdout.strip()}\nstderr: {proc.stderr.strip()}" + _log_tail(log)
        )


def silent_install(setup: Path, install_dir: Path, log: Path) -> None:
    # /SUPPRESSMSGBOXES is mandatory, not cosmetic: the .iss shows a MsgBox from
    # [Code] at ssPostInstall, and that code path runs even under /VERYSILENT.
    # Without it an unattended install blocks forever on a dialog nobody can click.
    _run(
        [
            str(setup),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/NOCANCEL",
            f"/DIR={install_dir}",
            f"/LOG={log}",
        ],
        "silent install",
        log=log,
    )


def silent_uninstall(install_dir: Path) -> None:
    uninstaller = uninstaller_path(install_dir)
    if not uninstaller.is_file():
        raise InstallerSmokeError(f"uninstaller missing: {uninstaller}")
    _run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], "silent uninstall")


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

        silent_install(setup, install_dir, log)
        print(f"  install -> {install_dir}")

        verify_installed_layout(install_dir)
        print(f"  layout  -> {', '.join(EXPECTED_AFTER_INSTALL)} present, api_key.txt non-empty")

        run_installed_binary(install_dir)
        print("  installed binary -> serves and shuts down")

        silent_uninstall(install_dir)
        verify_removed(install_dir)
        print("  uninstall -> binary and install dir removed")
    except InstallerSmokeError as exc:
        print(f"\nINSTALLER SMOKE FAILED: {exc}", file=sys.stderr)
        return 1

    print("\nINSTALLER SMOKE OK — installs, runs, uninstalls clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
