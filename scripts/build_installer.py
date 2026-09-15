#!/usr/bin/env python3
"""Installer packaging helper: validate, build-portable, build-installer, package-linux, checksums.

On Linux: validate + checksums always work; build-portable produces a Linux one-file
smoke binary (or documents windows-only if PyInstaller missing). package-linux wraps
the ELF + installer/linux/* into zip/tar.gz and (N036) deb/rpm/AppImage.
Flatpak is out of scope (#64).

Setup.exe requires Windows + Inno Setup (ISCC) — use GHA windows-latest
(see .github/workflows/build.yml / build-linux.yml).

Linux package = FastAPI server portable binary (LinuxBackend real OS by default; Fake optional),
NOT a Windows emulator. Windows EXE uses WindowsBackend when on Win32.
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

    # N034 identity helpers + least-privilege service account
    for helper in ("write_secure_api_key.ps1", "harden_service_dirs.ps1"):
        hp = SERVICE_SCRIPTS / helper
        ok = hp.is_file()
        checks[f"helper_{helper}"] = ok
        if not ok:
            errors.append(f"missing N034 helper: installer/service_scripts/{helper}")

    nssm_bat = SERVICE_SCRIPTS / "install_nssm.bat"
    if nssm_bat.is_file():
        nt = _read(nssm_bat)
        checks["nssm_objectname_localservice"] = (
            "ObjectName" in nt and ("NT AUTHORITY" + chr(92) + "LocalService") in nt
        )
        checks["nssm_harden_dirs"] = "harden_service_dirs.ps1" in nt
        checks["nssm_weak_key_deny"] = "dev-key-change-me" in nt
        checks["nssm_appdirectory"] = "AppDirectory" in nt
        if not checks["nssm_objectname_localservice"]:
            errors.append("install_nssm.bat must set ObjectName to NT AUTHORITY\\LocalService (N034)")
        if not checks["nssm_harden_dirs"]:
            errors.append("install_nssm.bat must call harden_service_dirs.ps1 (N034)")
        if not checks["nssm_weak_key_deny"]:
            errors.append("install_nssm.bat must denylist weak/dev API keys (N034)")

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
        checks["iss_setup_mutex"] = "SetupMutex" in it and "WinOsApiSetupMutex" in it
        checks["iss_secure_key_script"] = "write_secure_api_key.ps1" in it
        if EXPECTED_EXE not in it:
            errors.append(f"winos-api.iss must reference {EXPECTED_EXE}")
        if "127.0.0.1" not in it:
            warnings.append("Inno script should default bind to 127.0.0.1 (localhost)")
        if not checks["iss_setup_mutex"]:
            errors.append("winos-api.iss must declare SetupMutex=WinOsApiSetupMutex (N034 single-instance wizard)")
        if not checks["iss_secure_key_script"]:
            errors.append("winos-api.iss must generate api_key via write_secure_api_key.ps1 (N034 CSPRNG+ACL)")

    # Linux unit / install.sh — N035: LinuxBackend + --api-key-file, no FakeBackend default
    if (LINUX_INSTALLER / "winos-api.service").is_file():
        unit = _read(LINUX_INSTALLER / "winos-api.service")
        checks["linux_unit_localhost"] = "127.0.0.1" in unit
        checks["linux_unit_api_key_file"] = "--api-key-file" in unit
        desc_line = next((ln for ln in unit.splitlines() if ln.startswith("Description=")), "")
        checks["linux_unit_backend_auto"] = (
            "WINOS_BACKEND=auto" in unit
            and "FakeBackend" not in desc_line
            and "FakeBackend" not in "".join(
                ln for ln in unit.splitlines() if ln.startswith("ExecStart=")
            )
        )
        checks["linux_unit_backend"] = "WINOS_BACKEND" in unit and "FakeBackend" not in desc_line
        if "127.0.0.1" not in unit:
            errors.append("linux unit must bind 127.0.0.1 (N035)")
        if not checks["linux_unit_api_key_file"]:
            errors.append("linux unit must pass --api-key-file (N035)")
        if "WINOS_BACKEND=auto" not in unit:
            errors.append("linux unit must set Environment=WINOS_BACKEND=auto (N035)")
        if "FakeBackend" in desc_line:
            errors.append("linux unit Description must not present FakeBackend as default (N035)")

    install_sh = LINUX_INSTALLER / "install.sh"
    if install_sh.is_file():
        sh = _read(install_sh)
        # Distributed install path must not push FakeBackend as product/default start
        checks["linux_install_no_fake_default"] = "FakeBackend" not in sh and "WINOS_BACKEND=fake" not in sh
        checks["linux_install_no_secret_print"] = "WINOS_API_KEYS=" not in sh
        checks["linux_install_api_key_file"] = "--api-key-file" in sh
        checks["linux_install_backend_auto"] = "WINOS_BACKEND=auto" in sh
        if not checks["linux_install_no_fake_default"]:
            errors.append(
                "install.sh must not instruct FakeBackend / WINOS_BACKEND=fake as default start (N035)"
            )
        if not checks["linux_install_no_secret_print"]:
            errors.append(
                "install.sh must not print WINOS_API_KEYS= with secret in final message (N035)"
            )
        if not checks["linux_install_api_key_file"]:
            errors.append("install.sh must mention --api-key-file (N035)")
        if not checks["linux_install_backend_auto"]:
            errors.append("install.sh must mention WINOS_BACKEND=auto (N035)")

    # N036 — deb/rpm/AppImage packaging templates + upgrade lifecycle flags
    # Load N036 helpers from scripts/ next to this file (not ROOT — tests may stub ROOT)
    import importlib.util

    _lpf = Path(__file__).resolve().parent / "linux_package_formats.py"
    validate_packaging_templates = None  # type: ignore[assignment]
    required_packaging_files = None  # type: ignore[assignment]
    if _lpf.is_file():
        _spec = importlib.util.spec_from_file_location("linux_package_formats", _lpf)
        if _spec and _spec.loader:
            _mod = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            validate_packaging_templates = _mod.validate_packaging_templates
            required_packaging_files = _mod.required_packaging_files

    if validate_packaging_templates is not None:
        pkg_errs = validate_packaging_templates(LINUX_INSTALLER)
        checks["linux_packaging_templates"] = not pkg_errs
        for e in pkg_errs:
            errors.append(e)
        req = required_packaging_files(LINUX_INSTALLER)
        for key, p in req.items():
            checks[f"n036_{key}"] = p.is_file()
    else:
        checks["linux_packaging_templates"] = False
        errors.append("scripts/linux_package_formats.py missing (N036)")

    if install_sh.is_file():
        sh2 = _read(install_sh)
        checks["linux_install_upgrade_flag"] = "--upgrade" in sh2 and "UPGRADE_MODE" in sh2
        checks["linux_install_preserve_key_on_upgrade"] = (
            "api_key.txt will be preserved" in sh2 or "UPGRADE_MODE" in sh2
        )
        if not checks["linux_install_upgrade_flag"]:
            errors.append("install.sh must support --upgrade (N036)")
    uninstall_sh = LINUX_INSTALLER / "uninstall.sh"
    if uninstall_sh.is_file():
        ush = _read(uninstall_sh)
        checks["linux_uninstall_keep_data"] = "--keep-data" in ush
        if not checks["linux_uninstall_keep_data"]:
            errors.append("uninstall.sh must support --keep-data (N036)")

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
    """Delegate to shared N028 helper (single implementation)."""
    from windows_os_api.update.checksum_manifest import sha256_file as _sha256

    return _sha256(Path(path))


def checksums(
    paths: list[Path] | None = None,
    out: Path | None = None,
    *,
    root: Path | None = None,
) -> Path:
    """Write sha256 checksums for artifacts (N028: no self-hash, unique labels).

    - Excludes the output manifest path even if passed explicitly (no self-hash).
    - Missing inputs raise ``ChecksumManifestError`` (fail-closed).
    - Duplicate labels fail closed; pass ``root=`` for unique relative paths.
    - Atomic write via ``write_checksum_manifest``.
    """
    from windows_os_api.update.checksum_manifest import (
        build_checksum_lines,
        write_checksum_manifest,
    )

    if out is None:
        out = DIST / "checksums.txt"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if paths is None:
        candidates: list[Path] = []
        seen: set[Path] = set()
        for folder in (DIST, OUTPUT, ROOT / "dist"):
            if folder.is_dir():
                for p in folder.iterdir():
                    if not p.is_file():
                        continue
                    if p.suffix.lower() == ".txt":
                        continue
                    rp = p.resolve()
                    if rp in seen:
                        continue
                    seen.add(rp)
                    candidates.append(p)
        paths = candidates

    # Default root=None keeps flat basename labels (duplicate basename -> fail).
    lines = build_checksum_lines(paths, out=out, root=root)
    written = write_checksum_manifest(paths, out, root=root)
    print(f"Wrote {written} ({len(lines)} files)")
    for line in lines:
        print(line)
    return written


def verify_checksums(manifest: Path, *, root: Path | None = None) -> int:
    """Independent verify client for a checksums manifest (H63-N028)."""
    from windows_os_api.update.checksum_manifest import (
        ChecksumManifestError,
        verify_checksum_manifest,
    )

    try:
        mapping = verify_checksum_manifest(Path(manifest), root=root)
    except ChecksumManifestError as exc:
        print(f"VERIFY FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"VERIFY OK ({len(mapping)} files) — {manifest}")
    for label, digest in sorted(mapping.items()):
        print(f"{digest}  {label}")
    return 0


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
    """Package Linux portable zip/tar.gz and N036 deb/rpm/AppImage.

    Requires dist/winos-api (or builds portable first when build_if_missing=True).
    Linux package = FastAPI LinuxBackend / real OS server (WINOS_BACKEND=auto),
    not a Windows emulator. FakeBackend remains an optional fixture only.
    Flatpak is out of scope (#64).
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
        print("dry-run: not writing zip/tar.gz/deb/rpm/AppImage")
        native = sorted({"deb", "rpm", "appimage"}.intersection(formats))
        if native:
            print(f"dry-run: would also build native formats: {native}")
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

    # N036 native packages
    native = {"deb", "rpm", "appimage"}
    if native.intersection(formats):
        import importlib.util
        import platform as _platform

        _lpf_path = Path(__file__).resolve().parent / "linux_package_formats.py"
        _spec = importlib.util.spec_from_file_location("linux_package_formats", _lpf_path)
        assert _spec and _spec.loader
        lpf = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(lpf)

        machine = _platform.machine() or "x86_64"
        unit_src = LINUX_INSTALLER / "winos-api.service"
        if dry_run:
            print("dry-run: would build", sorted(native.intersection(formats)))
        else:
            if "deb" in formats:
                deb_path = lpf.build_deb(
                    binary=binary,
                    version=version,
                    arch=machine,
                    linux_installer=LINUX_INSTALLER,
                    dist=DIST,
                    unit_src=unit_src,
                    dry_run=False,
                )
                print(f"wrote {deb_path} ({deb_path.stat().st_size} bytes)")
                produced.append(deb_path)
            if "rpm" in formats:
                rpm_path = lpf.build_rpm(
                    binary=binary,
                    version=version,
                    arch=machine,
                    linux_installer=LINUX_INSTALLER,
                    dist=DIST,
                    unit_src=unit_src,
                    dry_run=False,
                )
                print(f"wrote {rpm_path} ({rpm_path.stat().st_size} bytes)")
                produced.append(rpm_path)
                spec_side = DIST / f"winos-api-{version}-1.spec"
                if spec_side.is_file():
                    produced.append(spec_side)
            if "appimage" in formats:
                app_path = lpf.build_appimage(
                    binary=binary,
                    version=version,
                    arch=machine,
                    linux_installer=LINUX_INSTALLER,
                    dist=DIST,
                    dry_run=False,
                )
                print(f"wrote {app_path} ({app_path.stat().st_size} bytes)")
                produced.append(app_path)

    # Also keep a copy of the raw binary referenced in checksums
    checksum_targets = list(produced)
    if binary.is_file():
        checksum_targets.append(binary)
    checksums(checksum_targets, DIST / "checksums-linux.txt")
    print(
        "NOTE: Linux portable = FastAPI server (LinuxBackend / real OS, "
        "WINOS_BACKEND=auto), not a Windows emulator. Use Windows artifacts for Win32."
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
        help="Linux portable zip/tar.gz + N036 deb/rpm/AppImage",
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
        choices=("zip", "tar.gz", "tgz", "deb", "rpm", "appimage"),
        default=["zip", "tar.gz"],
        help="Formats to produce (default: zip tar.gz; N036 also: deb rpm appimage)",
    )
    p_sum = sub.add_parser("checksums", help="Write sha256 for dist/installer artifacts")
    p_sum.add_argument("files", nargs="*", help="Optional explicit files")
    p_sum.add_argument("-o", "--output", default=None, help="Output checksums.txt path")
    p_sum.add_argument(
        "--root",
        default=None,
        help="Label root for unique relative paths (default: basename-only mode)",
    )
    p_ver = sub.add_parser(
        "verify-checksums",
        help="Independently verify a checksums manifest (N028)",
    )
    p_ver.add_argument("manifest", help="Path to checksums.txt / SHA256SUMS.txt")
    p_ver.add_argument(
        "--root",
        default=None,
        help="Directory that relative labels resolve against (default: manifest parent)",
    )

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
        from windows_os_api.update.checksum_manifest import ChecksumManifestError

        paths = [Path(f) for f in args.files] if args.files else None
        out = Path(args.output) if args.output else None
        root = Path(args.root) if getattr(args, "root", None) else None
        try:
            checksums(paths, out, root=root)
        except ChecksumManifestError as exc:
            print(f"CHECKSUMS FAIL: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "verify-checksums":
        root = Path(args.root) if args.root else None
        return verify_checksums(Path(args.manifest), root=root)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
