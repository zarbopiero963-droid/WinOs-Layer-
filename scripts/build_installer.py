#!/usr/bin/env python3
"""Installer packaging helper: validate, build-portable, build-installer, package-linux, checksums.

On Linux: validate + checksums always work; build-portable produces a Linux one-file
smoke binary (or documents windows-only if PyInstaller missing). package-linux wraps
the ELF + installer/linux/* into winos-api-portable-linux.zip/.tar.gz.

Setup.exe requires Windows + Inno Setup (ISCC) — use GHA windows-latest
(see .github/workflows/build.yml / build-linux.yml).

Linux package = FastAPI server portable binary (FakeBackend-capable), NOT a Windows emulator.
Windows EXE uses WindowsBackend when on Win32.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "installer" / "pyinstaller" / "winos-api.spec"
ISS = ROOT / "installer" / "inno" / "winos-api.iss"
SERVICE_SCRIPTS = ROOT / "installer" / "service_scripts"
LINUX_INSTALLER = ROOT / "installer" / "linux"
DIST = ROOT / "dist"
OUTPUT = ROOT / "installer" / "output"

EXPECTED_SERVICE = "WindowsOSLayerService"
EXPECTED_ENTRY = "winos-api"
EXPECTED_EXE = "winos-api"
LINUX_ZIP_NAME = "winos-api-portable-linux.zip"
LINUX_TGZ_NAME = "winos-api-portable-linux.tar.gz"
LINUX_STAGE_NAME = "winos-api-portable-linux"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def read_package_version() -> str:
    """Return version string from pyproject.toml (fallback 0.0.0)."""
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return "0.0.0"
    text = _read(pyproject)
    m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return m.group(1) if m else "0.0.0"


def validate(verbose: bool = True) -> dict[str, Any]:
    """Check packaging files exist and are consistent."""
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {}

    required = {
        "pyinstaller_spec": SPEC,
        "inno_iss": ISS,
        "install_nssm": SERVICE_SCRIPTS / "install_nssm.bat",
        "install_sc": SERVICE_SCRIPTS / "install_sc.bat",
        "uninstall_service": SERVICE_SCRIPTS / "uninstall_service.bat",
        "cli_main": ROOT / "windows_os_api" / "cli" / "main.py",
        "service_module": ROOT / "windows_os_api" / "installer" / "service.py",
        "control_center": ROOT / "windows_os_api" / "control_center" / "index.html",
        "linux_service": LINUX_INSTALLER / "winos-api.service",
        "linux_install_sh": LINUX_INSTALLER / "install.sh",
        "linux_uninstall_sh": LINUX_INSTALLER / "uninstall.sh",
    }
    for key, path in required.items():
        ok = path.is_file()
        checks[key] = ok
        if not ok:
            errors.append(f"missing: {path.relative_to(ROOT)}")

    # Optional but recommended Linux docs
    linux_md = LINUX_INSTALLER / "LINUX.md"
    checks["linux_md"] = linux_md.is_file()
    if not linux_md.is_file():
        warnings.append("installer/linux/LINUX.md missing")

    # pyproject entry point
    pyproject = ROOT / "pyproject.toml"
    if pyproject.is_file():
        ppt = _read(pyproject)
        checks["entry_winos_api"] = f'{EXPECTED_ENTRY} =' in ppt or f'"{EXPECTED_ENTRY}"' in ppt
        if "winos-api" not in ppt:
            errors.append("pyproject.toml missing winos-api console script")
        else:
            checks["entry_winos_api"] = True
    else:
        errors.append("missing pyproject.toml")
        checks["entry_winos_api"] = False

    # Service name consistency
    svc_mod = ROOT / "windows_os_api" / "installer" / "service.py"
    if svc_mod.is_file():
        st = _read(svc_mod)
        checks["service_name_module"] = EXPECTED_SERVICE in st
        if EXPECTED_SERVICE not in st:
            errors.append(f"service.py must define SERVICE_NAME={EXPECTED_SERVICE}")

    for bat in ("install_nssm.bat", "install_sc.bat", "uninstall_service.bat"):
        bp = SERVICE_SCRIPTS / bat
        if bp.is_file():
            bt = _read(bp)
            ok = EXPECTED_SERVICE in bt
            checks[f"service_name_{bat}"] = ok
            if not ok:
                errors.append(f"{bat} missing service name {EXPECTED_SERVICE}")

    # Spec / ISS consistency
    if SPEC.is_file():
        st = _read(SPEC)
        checks["spec_entry"] = "cli/main.py" in st.replace("\\", "/")
        checks["spec_name"] = f"name='{EXPECTED_EXE}'" in st or f'name="{EXPECTED_EXE}"' in st
        if not checks["spec_entry"]:
            errors.append("winos-api.spec must reference windows_os_api/cli/main.py")
        if not checks["spec_name"]:
            errors.append(f"winos-api.spec EXE name must be {EXPECTED_EXE}")

    if ISS.is_file():
        it = _read(ISS)
        checks["iss_exe"] = EXPECTED_EXE in it
        checks["iss_service_scripts"] = "service_scripts" in it or "service" in it.lower()
        checks["iss_localhost"] = "127.0.0.1" in it
        if EXPECTED_EXE not in it:
            errors.append(f"winos-api.iss must reference {EXPECTED_EXE}")
        if "127.0.0.1" not in it:
            warnings.append("Inno script should default bind to 127.0.0.1 (localhost)")

    # Linux unit should bind localhost and mention FakeBackend / WINOS_BACKEND
    if (LINUX_INSTALLER / "winos-api.service").is_file():
        unit = _read(LINUX_INSTALLER / "winos-api.service")
        checks["linux_unit_localhost"] = "127.0.0.1" in unit
        checks["linux_unit_backend"] = "WINOS_BACKEND" in unit or "FakeBackend" in unit
        if "127.0.0.1" not in unit:
            warnings.append("linux unit should bind 127.0.0.1")

    # Localhost firewall note in README
    readme = ROOT / "installer" / "README.md"
    if readme.is_file():
        rt = _read(readme)
        checks["installer_readme"] = "127.0.0.1" in rt or "localhost" in rt.lower()
    else:
        checks["installer_readme"] = False
        warnings.append("installer/README.md missing")

    result = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "service_name": EXPECTED_SERVICE,
        "entry_point": EXPECTED_ENTRY,
        "platform": sys.platform,
        "version": read_package_version(),
    }
    if verbose:
        print(json.dumps(result, indent=2))
        if errors:
            print(f"VALIDATE FAIL ({len(errors)} errors)", file=sys.stderr)
        else:
            print("VALIDATE OK")
    return result


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def checksums(paths: list[Path] | None = None, out: Path | None = None) -> Path:
    """Write sha256 checksums for artifacts."""
    if paths is None:
        candidates = []
        for folder in (DIST, OUTPUT, ROOT / "dist"):
            if folder.is_dir():
                candidates.extend(
                    p for p in folder.iterdir() if p.is_file() and not p.name.endswith(".txt")
                )
        paths = sorted(set(candidates), key=lambda p: str(p))
    if out is None:
        out = DIST / "checksums.txt"
        out.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for p in paths:
        if not p.is_file():
            continue
        digest = sha256_file(p)
        lines.append(f"{digest}  {p.name}")
    out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print(f"Wrote {out} ({len(lines)} files)")
    for line in lines:
        print(line)
    return out


def build_portable(dry_run: bool = False) -> int:
    """PyInstaller onefile for winos-api on the current OS."""
    v = validate(verbose=False)
    if not v["ok"]:
        print("validate failed; refusing build-portable", file=sys.stderr)
        for e in v["errors"]:
            print(f"  - {e}", file=sys.stderr)
        return 1

    pyinstaller = shutil.which("pyinstaller")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(SPEC),
    ]
    print(f"platform={sys.platform} machine={platform.machine()}")
    print(f"cmd: {' '.join(cmd)}")
    if dry_run:
        print("dry-run: not invoking PyInstaller")
        return 0

    # Prefer python -m PyInstaller
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), check=False)
    except Exception as e:  # noqa: BLE001
        print(f"PyInstaller failed to start: {e}", file=sys.stderr)
        if pyinstaller:
            r = subprocess.run([pyinstaller, "--noconfirm", "--clean", str(SPEC)], cwd=str(ROOT))
        else:
            print(
                "Install: pip install pyinstaller\n"
                "Then: python scripts/build_installer.py build-portable",
                file=sys.stderr,
            )
            return 1
        return r.returncode

    if r.returncode != 0:
        print("PyInstaller failed", file=sys.stderr)
        return r.returncode

    # Locate artifact
    exe_name = EXPECTED_EXE + (".exe" if sys.platform == "win32" else "")
    artifact = DIST / exe_name
    if not artifact.exists():
        # Linux may produce without suffix
        matches = list(DIST.glob("winos-api*"))
        print(f"dist contents: {[p.name for p in DIST.iterdir()] if DIST.exists() else []}")
        if matches:
            artifact = matches[0]
    if artifact.exists():
        print(f"portable artifact: {artifact} ({artifact.stat().st_size} bytes)")
        checksums([artifact], DIST / "checksums.txt")
    else:
        print("WARNING: no portable artifact found in dist/", file=sys.stderr)
    return 0


def build_installer(dry_run: bool = False) -> int:
    """Invoke Inno Setup Compiler (ISCC) if present."""
    v = validate(verbose=False)
    if not v["ok"]:
        print("validate failed; refusing build-installer", file=sys.stderr)
        for e in v["errors"]:
            print(f"  - {e}", file=sys.stderr)
        return 1

    iscc = shutil.which("ISCC") or shutil.which("iscc")
    # Common Windows install paths
    if not iscc and sys.platform == "win32":
        for candidate in (
            Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
            Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        ):
            if candidate.is_file():
                iscc = str(candidate)
                break

    exe = DIST / "winos-api.exe"
    print(f"ISCC={iscc!r} winos-api.exe exists={exe.exists()}")

    if dry_run:
        print("dry-run: would invoke ISCC on", ISS)
        return 0

    if sys.platform != "win32":
        print(
            "Setup.exe cannot be built on this platform.\n"
            "Exact steps (Windows / GHA windows-latest):\n"
            "  1. choco install innosetup -y   # or install Inno Setup 6\n"
            "  2. pip install -e '.[dev]' pyinstaller\n"
            "  3. python scripts/build_installer.py build-portable\n"
            "  4. python scripts/build_installer.py build-installer\n"
            "  5. python scripts/build_installer.py checksums\n"
            "Artifacts land in dist/ and installer/output/.\n"
            "See .github/workflows/build.yml for automated Windows build.",
            file=sys.stderr,
        )
        return 2

    if not iscc:
        print(
            "ISCC not found. Install Inno Setup 6, then re-run.\n"
            "  choco install innosetup -y\n"
            f"  ISCC {ISS}",
            file=sys.stderr,
        )
        return 1

    if not exe.exists():
        print("dist/winos-api.exe missing — run build-portable first", file=sys.stderr)
        return 1

    OUTPUT.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([iscc, str(ISS)], cwd=str(ROOT))
    if r.returncode == 0:
        arts = list(OUTPUT.glob("*.exe")) if OUTPUT.exists() else []
        print(f"installer artifacts: {arts}")
        if arts:
            checksums(arts, OUTPUT / "checksums.txt")
    return r.returncode


def _find_linux_binary() -> Path | None:
    """Locate dist/winos-api (ELF) — ignore .zip/.tar.gz/.txt/.exe."""
    candidates = [
        DIST / EXPECTED_EXE,
        DIST / f"{EXPECTED_EXE}.bin",
    ]
    for c in candidates:
        if c.is_file() and not c.name.endswith((".zip", ".tar.gz", ".txt", ".exe")):
            return c
    if DIST.is_dir():
        for p in sorted(DIST.iterdir()):
            if not p.is_file():
                continue
            name = p.name
            if name.startswith("winos-api") and not name.endswith(
                (".zip", ".tar.gz", ".tgz", ".txt", ".exe", ".whl")
            ):
                return p
    return None


def package_linux(
    dry_run: bool = False,
    build_if_missing: bool = False,
    formats: tuple[str, ...] = ("zip", "tar.gz"),
) -> int:
    """Package Linux portable zip/tar.gz with binary + installer/linux/* + VERSION.

    Requires dist/winos-api (or builds portable first when build_if_missing=True).
    Linux package = FastAPI FakeBackend-capable server, not a Windows emulator.
    """
    v = validate(verbose=False)
    if not v["ok"]:
        print("validate failed; refusing package-linux", file=sys.stderr)
        for e in v["errors"]:
            print(f"  - {e}", file=sys.stderr)
        return 1

    if not LINUX_INSTALLER.is_dir():
        print(f"missing {LINUX_INSTALLER}", file=sys.stderr)
        return 1

    binary = _find_linux_binary()
    if binary is None:
        if build_if_missing and not dry_run:
            print("dist/winos-api missing — invoking build-portable first")
            rc = build_portable(dry_run=False)
            if rc != 0:
                return rc
            binary = _find_linux_binary()
        if binary is None:
            print(
                "dist/winos-api missing. Run:\n"
                "  python scripts/build_installer.py build-portable\n"
                "  python scripts/build_installer.py package-linux\n"
                "Or: python scripts/build_installer.py package-linux --build",
                file=sys.stderr,
            )
            if dry_run:
                print("dry-run: would package once binary exists")
                return 0
            return 1

    version = read_package_version()
    DIST.mkdir(parents=True, exist_ok=True)
    stage = DIST / LINUX_STAGE_NAME
    print(f"package-linux: binary={binary} version={version}")
    print(f"formats={formats} stage={stage}")

    if dry_run:
        print("dry-run: not writing zip/tar.gz")
        return 0

    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    # Binary at package root
    dest_bin = stage / EXPECTED_EXE
    shutil.copy2(binary, dest_bin)
    dest_bin.chmod(dest_bin.stat().st_mode | 0o111)

    # installer/linux/*
    linux_dest = stage / "installer" / "linux"
    linux_dest.mkdir(parents=True)
    for src in sorted(LINUX_INSTALLER.iterdir()):
        if src.is_file():
            shutil.copy2(src, linux_dest / src.name)
            if src.suffix == ".sh":
                (linux_dest / src.name).chmod(
                    (linux_dest / src.name).stat().st_mode | 0o111
                )

    # Top-level LINUX.md / README convenience copies
    for name in ("LINUX.md",):
        src = LINUX_INSTALLER / name
        if src.is_file():
            shutil.copy2(src, stage / name)
    installer_readme = ROOT / "installer" / "README.md"
    if installer_readme.is_file():
        shutil.copy2(installer_readme, stage / "README.md")

    (stage / "VERSION").write_text(version + "\n", encoding="utf-8")

    produced: list[Path] = []

    if "zip" in formats:
        zip_path = DIST / LINUX_ZIP_NAME
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(stage.rglob("*")):
                if p.is_file():
                    zf.write(p, str(Path(LINUX_STAGE_NAME) / p.relative_to(stage)))
        print(f"wrote {zip_path} ({zip_path.stat().st_size} bytes)")
        produced.append(zip_path)

    if "tar.gz" in formats or "tgz" in formats:
        tgz_path = DIST / LINUX_TGZ_NAME
        if tgz_path.exists():
            tgz_path.unlink()
        with tarfile.open(tgz_path, "w:gz") as tf:
            tf.add(stage, arcname=LINUX_STAGE_NAME)
        print(f"wrote {tgz_path} ({tgz_path.stat().st_size} bytes)")
        produced.append(tgz_path)

    # Also keep a copy of the raw binary referenced in checksums
    checksum_targets = list(produced)
    if binary.is_file():
        checksum_targets.append(binary)
    checksums(checksum_targets, DIST / "checksums-linux.txt")
    print(
        "NOTE: Linux portable = FastAPI server (FakeBackend-capable), "
        "not a Windows emulator. Use Windows artifacts for Win32."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WinOs-Layer installer build helper")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without building")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate", help="Check spec/.iss/scripts consistency")
    sub.add_parser("build-portable", help="PyInstaller onefile for current OS")
    sub.add_parser("build-installer", help="Compile Inno Setup (Windows + ISCC)")
    p_linux = sub.add_parser(
        "package-linux",
        help="Zip/tar.gz Linux portable + installer/linux + VERSION",
    )
    p_linux.add_argument(
        "--build",
        action="store_true",
        help="Run build-portable first if dist/winos-api is missing",
    )
    p_linux.add_argument(
        "--format",
        dest="formats",
        nargs="+",
        choices=("zip", "tar.gz", "tgz"),
        default=["zip", "tar.gz"],
        help="Archive formats to produce (default: zip tar.gz)",
    )
    p_sum = sub.add_parser("checksums", help="Write sha256 for dist/installer artifacts")
    p_sum.add_argument("files", nargs="*", help="Optional explicit files")
    p_sum.add_argument("-o", "--output", default=None, help="Output checksums.txt path")

    args = parser.parse_args(argv)

    if args.cmd == "validate":
        return 0 if validate()["ok"] else 1
    if args.cmd == "build-portable":
        return build_portable(dry_run=args.dry_run)
    if args.cmd == "build-installer":
        return build_installer(dry_run=args.dry_run)
    if args.cmd == "package-linux":
        formats = tuple(args.formats)
        return package_linux(
            dry_run=args.dry_run,
            build_if_missing=args.build,
            formats=formats,
        )
    if args.cmd == "checksums":
        paths = [Path(f) for f in args.files] if args.files else None
        out = Path(args.output) if args.output else None
        checksums(paths, out)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
