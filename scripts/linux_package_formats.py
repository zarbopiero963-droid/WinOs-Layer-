#!/usr/bin/env python3
"""N036 — Build deb / rpm / AppImage artifacts with coherent metadata + lifecycle.

Produces package *artifacts and metadata* on the build host. Real distro
install/upgrade/uninstall remains MANUAL_ONLY / #21 — do not claim installed PASS.

Flatpak is intentionally out of scope (issue #64).
"""
from __future__ import annotations

import gzip
import io
import os
import shutil
import struct
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

PACKAGE_NAME = "winos-api"


class PackagingError(RuntimeError):
    """Fail-closed packaging error (N036)."""
DEB_ARCH_MAP = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
RPM_ARCH_MAP = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}


def normalize_deb_arch(machine: str) -> str:
    return DEB_ARCH_MAP.get(machine.lower(), "amd64")


def normalize_rpm_arch(machine: str) -> str:
    return RPM_ARCH_MAP.get(machine.lower(), "x86_64")


def render_template(text: str, *, version: str, arch: str) -> str:
    return (
        text.replace("@VERSION@", version)
        .replace("@ARCH@", arch)
        .replace("@PACKAGE@", PACKAGE_NAME)
    )


def packaging_root(linux_installer: Path) -> Path:
    return linux_installer / "packaging"


def required_packaging_files(linux_installer: Path) -> dict[str, Path]:
    root = packaging_root(linux_installer)
    return {
        "debian_control_in": root / "debian" / "control.in",
        "debian_postinst": root / "debian" / "postinst",
        "debian_prerm": root / "debian" / "prerm",
        "debian_postrm": root / "debian" / "postrm",
        "rpm_spec_in": root / "rpm" / "winos-api.spec.in",
        "appimage_apprun": root / "appimage" / "AppRun",
        "appimage_desktop": root / "appimage" / "winos-api.desktop",
    }


def validate_packaging_templates(linux_installer: Path) -> list[str]:
    """Return list of error strings (empty = OK)."""
    errors: list[str] = []
    files = required_packaging_files(linux_installer)
    for key, path in files.items():
        if not path.is_file():
            errors.append(f"missing N036 packaging template: {path}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if key == "debian_control_in":
            for needle in ("Package:", "Version: @VERSION@", "Architecture: @ARCH@", "Description:"):
                if needle not in text:
                    errors.append(f"debian control.in missing {needle!r}")
            if "Flatpak" not in text and "flatpak" not in text.lower():
                # soft: docs mention flatpak out of scope in Description — optional
                pass
        if key.startswith("debian_") and key != "debian_control_in":
            if "api_key" not in text and key == "debian_postinst":
                errors.append("debian postinst must handle api_key preserve/generate")
            if key == "debian_postrm" and "purge" not in text:
                errors.append("debian postrm must distinguish purge vs remove")
        if key == "rpm_spec_in":
            for needle in ("%pre", "%post", "%preun", "%postun", "api_key", "@VERSION@"):
                if needle not in text:
                    errors.append(f"rpm spec missing {needle!r}")
        if key == "appimage_apprun" and "WINOS_BACKEND" not in text:
            errors.append("AppRun must default WINOS_BACKEND")
        if key == "appimage_desktop" and "Name=" not in text:
            errors.append("desktop file missing Name=")
    return errors


def _copy_binary(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    dest.chmod(dest.stat().st_mode | 0o111)


def _ar_member(name: str, data: bytes) -> bytes:
    """GNU ar member header (60 bytes) + data, padded to even size."""
    name_field = (name + "/").encode("ascii")[:16].ljust(16)
    header = (
        name_field
        + b"0".ljust(12)  # mtime
        + b"0".ljust(6)  # uid
        + b"0".ljust(6)  # gid
        + b"100644".ljust(8)  # mode
        + str(len(data)).encode("ascii").ljust(10)
        + b"`\n"
    )
    assert len(header) == 60
    out = header + data
    if len(data) % 2 == 1:
        out += b"\n"
    return out


def _tar_gz_from_dir(root: Path, *, arc_root: str = ".") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in sorted(root.rglob("*")):
            if not p.is_file() and not p.is_symlink():
                continue
            rel = p.relative_to(root).as_posix()
            arcname = f"{arc_root}/{rel}" if arc_root != "." else rel
            tf.add(p, arcname=arcname)
    return buf.getvalue()


def _stage_deb_tree(
    *,
    binary: Path,
    version: str,
    control: str,
    files: dict[str, Path],
    unit_src: Path,
    linux_installer: Path,
    root: Path,
) -> None:
    debian = root / "DEBIAN"
    opt = root / "opt" / "winos-api"
    unit_dir = root / "etc" / "systemd" / "system"
    bin_dir = root / "usr" / "local" / "bin"
    doc = root / "usr" / "share" / "doc" / "winos-api"
    for d in (debian, opt, unit_dir, bin_dir, doc):
        d.mkdir(parents=True)

    _copy_binary(binary, opt / "winos-api")
    (opt / "VERSION").write_text(version + "\n", encoding="utf-8")
    shutil.copy2(unit_src, unit_dir / "winos-api.service")
    linux_md = linux_installer / "LINUX.md"
    if linux_md.is_file():
        shutil.copy2(linux_md, doc / "LINUX.md")
    # Portable wrapper instead of symlink (Windows staging cannot always symlink)
    (bin_dir / "winos-api").write_text(
        "#!/bin/sh\nexec /opt/winos-api/winos-api \"$@\"\n", encoding="utf-8"
    )
    (bin_dir / "winos-api").chmod(0o755)

    (debian / "control").write_text(control, encoding="utf-8")
    for script in ("postinst", "prerm", "postrm"):
        src = files[f"debian_{script}"]
        dest = debian / script
        shutil.copy2(src, dest)
        dest.chmod(0o755)


def build_deb_python(root: Path, out_path: Path) -> Path:
    """Assemble a valid .deb (ar of debian-binary + control.tar.gz + data.tar.gz)."""
    debian = root / "DEBIAN"
    # control archive: contents of DEBIAN/
    control_buf = io.BytesIO()
    with tarfile.open(fileobj=control_buf, mode="w:gz") as tf:
        for p in sorted(debian.iterdir()):
            if p.is_file():
                tf.add(p, arcname=p.name)
    control_tar = control_buf.getvalue()

    # data archive: everything except DEBIAN/
    data_buf = io.BytesIO()
    with tarfile.open(fileobj=data_buf, mode="w:gz") as tf:
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            if "DEBIAN" in p.parts:
                continue
            rel = p.relative_to(root).as_posix()
            tf.add(p, arcname=rel)
    data_tar = data_buf.getvalue()

    debian_binary = b"2.0\n"
    blob = (
        b"!<arch>\n"
        + _ar_member("debian-binary", debian_binary)
        + _ar_member("control.tar.gz", control_tar)
        + _ar_member("data.tar.gz", data_tar)
    )
    out_path.write_bytes(blob)
    return out_path


def build_deb(
    *,
    binary: Path,
    version: str,
    arch: str,
    linux_installer: Path,
    dist: Path,
    unit_src: Path,
    dry_run: bool = False,
) -> Path:
    """Build a .deb via dpkg-deb when available, else pure-Python ar fallback (N036)."""
    deb_arch = normalize_deb_arch(arch)
    out_name = f"{PACKAGE_NAME}_{version}_{deb_arch}.deb"
    out_path = dist / out_name
    files = required_packaging_files(linux_installer)
    control = render_template(
        files["debian_control_in"].read_text(encoding="utf-8"),
        version=version,
        arch=deb_arch,
    )

    if dry_run:
        return out_path

    dist.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="winos-deb-") as tmp:
        root = Path(tmp) / "pkg"
        _stage_deb_tree(
            binary=binary,
            version=version,
            control=control,
            files=files,
            unit_src=unit_src,
            linux_installer=linux_installer,
            root=root,
        )
        if out_path.exists():
            out_path.unlink()

        dpkg_deb = shutil.which("dpkg-deb")
        if dpkg_deb:
            r = subprocess.run(
                [dpkg_deb, "--build", "--root-owner-group", str(root), str(out_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            if r.returncode == 0 and out_path.is_file():
                return out_path
            # fall through to Python builder
        build_deb_python(root, out_path)
        if not out_path.is_file():
            raise RuntimeError("failed to write .deb artifact")
    return out_path

def _cpio_entry(name: str, data: bytes, mode: int = 0o644) -> bytes:
    """New ASCII (SVR4) cpio entry."""
    name_b = name.encode("utf-8")
    if not name_b.endswith(b"\0"):
        # cpio names are NUL-terminated in the name field length including NUL
        pass
    namesize = len(name_b) + 1  # include NUL
    header = (
        b"070701"
        + f"{0:08X}".encode()  # ino
        + f"{mode:08X}".encode()
        + f"{0:08X}".encode()  # uid
        + f"{0:08X}".encode()  # gid
        + f"{1:08X}".encode()  # nlink
        + f"{0:08X}".encode()  # mtime
        + f"{len(data):08X}".encode()
        + f"{0:08X}".encode()  # devmajor
        + f"{0:08X}".encode()  # devminor
        + f"{0:08X}".encode()  # rdevmajor
        + f"{0:08X}".encode()  # rdevminor
        + f"{namesize:08X}".encode()
        + f"{0:08X}".encode()  # check
    )
    name_field = name_b + b"\0"
    # header+name padded to 4-byte boundary
    pad1 = (4 - ((len(header) + len(name_field)) % 4)) % 4
    body = header + name_field + (b"\0" * pad1) + data
    pad2 = (4 - (len(data) % 4)) % 4
    return body + (b"\0" * pad2)


def _rpm_header_store(entries: list[tuple[int, int, bytes]]) -> tuple[bytes, bytes]:
    """Build RPM header index+store. entries: (tag, type, data_bytes).

    Types: 6=STRING, 4=INT32, 7=BIN, 8=STRING_ARRAY, 9=I18NSTRING
    """
    # Sort by tag for stability
    entries = sorted(entries, key=lambda e: e[0])
    store = bytearray()
    index = bytearray()
    for tag, typ, data in entries:
        # Align store for INT32
        if typ == 4:
            while len(store) % 4:
                store.append(0)
        offset = len(store)
        count = 1
        if typ in (6, 9):  # STRING / I18NSTRING — NUL terminated
            if not data.endswith(b"\0"):
                data = data + b"\0"
            store.extend(data)
        elif typ == 8:  # STRING_ARRAY — one string for us
            if not data.endswith(b"\0"):
                data = data + b"\0"
            store.extend(data)
        elif typ == 4:  # INT32
            store.extend(data)
        elif typ == 7:  # BIN
            store.extend(data)
            count = len(data)
        else:
            store.extend(data)
        index.extend(struct.pack("!IIII", tag, typ, offset, count))
    # Pad store to 8-byte boundary
    while len(store) % 8:
        store.append(0)
    return bytes(index), bytes(store)


def build_rpm_bytes(
    *,
    binary: Path,
    version: str,
    arch: str,
    unit_text: str,
    version_text: str,
    linux_md: str | None = None,
) -> bytes:
    """Construct a minimal binary RPM (lead + signature stub + header + gzip cpio)."""
    rpm_arch = normalize_rpm_arch(arch)
    release = "1"

    # Payload files (absolute paths as installed)
    files: list[tuple[str, bytes, int]] = [
        (f"./opt/winos-api/winos-api", binary.read_bytes(), 0o100755),
        (f"./opt/winos-api/VERSION", version_text.encode("utf-8"), 0o100644),
        (f"./etc/systemd/system/winos-api.service", unit_text.encode("utf-8"), 0o100644),
        (f"./usr/local/bin/winos-api", b"", 0o120777),  # symlink — cpio symlink special
    ]
    # Represent symlink: mode as symlink, data = target path
    # For simplicity ship a small wrapper script instead of symlink in minimal RPM
    files[-1] = (
        "./usr/local/bin/winos-api",
        b"#!/bin/sh\nexec /opt/winos-api/winos-api \"$@\"\n",
        0o100755,
    )
    if linux_md:
        files.append(
            ("./usr/share/doc/winos-api/LINUX.md", linux_md.encode("utf-8"), 0o100644)
        )

    cpio = bytearray()
    for name, data, mode in files:
        cpio.extend(_cpio_entry(name, data, mode=mode))
    cpio.extend(_cpio_entry("TRAILER!!!", b"", mode=0))
    payload = gzip.compress(bytes(cpio), mtime=0)

    name = PACKAGE_NAME
    # RPM tags (subset)
    RPMTAG_NAME = 1000
    RPMTAG_VERSION = 1001
    RPMTAG_RELEASE = 1002
    RPMTAG_SUMMARY = 1004
    RPMTAG_DESCRIPTION = 1005
    RPMTAG_BUILDTIME = 1006
    RPMTAG_SIZE = 1009
    RPMTAG_LICENSE = 1014
    RPMTAG_GROUP = 1016
    RPMTAG_PAYLOAD = 1124  # PAYLOADFORMAT? actually 1124 is PAYLOADFORMAT
    RPMTAG_PAYLOADCOMPRESSOR = 1125
    RPMTAG_PAYLOADFLAGS = 1126
    RPMTAG_ARCH = 1022
    RPMTAG_OS = 1021
    RPMTAG_SOURCERPM = 1044
    RPMTAG_PAYLOADFORMAT = 1124

    summary = b"WinOs-Layer Linux FastAPI OS API server (LinuxBackend)"
    description = (
        b"WinOs-Layer portable FastAPI server for Linux. "
        b"Default WINOS_BACKEND=auto (LinuxBackend). Upgrade preserves api_key.txt."
    )
    entries = [
        (RPMTAG_NAME, 6, name.encode()),
        (RPMTAG_VERSION, 6, version.encode()),
        (RPMTAG_RELEASE, 6, release.encode()),
        (RPMTAG_SUMMARY, 9, summary),
        (RPMTAG_DESCRIPTION, 9, description),
        (RPMTAG_BUILDTIME, 4, struct.pack("!I", 0)),
        (RPMTAG_SIZE, 4, struct.pack("!I", sum(len(d) for _, d, _ in files))),
        (RPMTAG_LICENSE, 6, b"MIT"),
        (RPMTAG_GROUP, 6, b"Applications/System"),
        (RPMTAG_OS, 6, b"linux"),
        (RPMTAG_ARCH, 6, rpm_arch.encode()),
        (RPMTAG_SOURCERPM, 6, b"(none)"),
        (RPMTAG_PAYLOADFORMAT, 6, b"cpio"),
        (RPMTAG_PAYLOADCOMPRESSOR, 6, b"gzip"),
        (RPMTAG_PAYLOADFLAGS, 6, b"9"),
    ]
    index, store = _rpm_header_store(entries)
    # Header magic: 8e ad e8 01 + reserved + nindex + hsize
    hdr = (
        bytes([0x8E, 0xAD, 0xE8, 0x01])
        + struct.pack("!I", 0)
        + struct.pack("!I", len(entries))
        + struct.pack("!I", len(store))
        + index
        + store
    )

    # Lead: 96 bytes
    magic = bytes([0xED, 0xAB, 0xEE, 0xDB])
    major_minor = bytes([3, 0])
    pkg_type = struct.pack("!H", 0)  # binary
    archnum = struct.pack("!H", 1)
    name_field = f"{name}-{version}-{release}".encode("ascii")[:66].ljust(66, b"\0")
    osnum = struct.pack("!H", 1)
    sig_type = struct.pack("!H", 5)
    reserved = b"\0" * 16
    lead = magic + major_minor + pkg_type + archnum + name_field + osnum + sig_type + reserved
    assert len(lead) == 96

    # Signature header: empty region header (still valid-ish for file(1) / structural tests)
    # Minimal signature: header with RPMSIGTAG_SIZE / PAYLOADSIZE
    RPMSIGTAG_SIZE = 1000
    RPMSIGTAG_PAYLOADSIZE = 1007
    sig_entries = [
        (RPMSIGTAG_SIZE, 4, struct.pack("!I", len(hdr) + len(payload))),
        (RPMSIGTAG_PAYLOADSIZE, 4, struct.pack("!I", len(payload))),
    ]
    s_index, s_store = _rpm_header_store(sig_entries)
    sig = (
        bytes([0x8E, 0xAD, 0xE8, 0x01])
        + struct.pack("!I", 0)
        + struct.pack("!I", len(sig_entries))
        + struct.pack("!I", len(s_store))
        + s_index
        + s_store
    )
    # Pad signature to 8-byte boundary
    pad = (8 - (len(sig) % 8)) % 8
    sig = sig + (b"\0" * pad)

    return lead + sig + hdr + payload


def build_rpm(
    *,
    binary: Path,
    version: str,
    arch: str,
    linux_installer: Path,
    dist: Path,
    unit_src: Path,
    dry_run: bool = False,
) -> Path:
    """Build .rpm (minimal writer) and always emit rendered .spec beside it."""
    rpm_arch = normalize_rpm_arch(arch)
    out_name = f"{PACKAGE_NAME}-{version}-1.{rpm_arch}.rpm"
    out_path = dist / out_name
    spec_out = dist / f"{PACKAGE_NAME}-{version}-1.spec"
    files = required_packaging_files(linux_installer)
    spec_text = render_template(
        files["rpm_spec_in"].read_text(encoding="utf-8"),
        version=version,
        arch=rpm_arch,
    )

    if dry_run:
        return out_path

    dist.mkdir(parents=True, exist_ok=True)
    spec_out.write_text(spec_text, encoding="utf-8")

    unit_text = unit_src.read_text(encoding="utf-8")
    linux_md_path = linux_installer / "LINUX.md"
    linux_md = linux_md_path.read_text(encoding="utf-8") if linux_md_path.is_file() else None
    rpm_bytes = build_rpm_bytes(
        binary=binary,
        version=version,
        arch=rpm_arch,
        unit_text=unit_text,
        version_text=version + "\n",
        linux_md=linux_md,
    )
    out_path.write_bytes(rpm_bytes)
    return out_path


def build_appimage(
    *,
    binary: Path,
    version: str,
    arch: str,
    linux_installer: Path,
    dist: Path,
    dry_run: bool = False,
) -> Path:
    """Build a Type-2 AppImage via ``appimagetool`` only (N036).

    Fail-closed: missing tool or non-zero exit must **not** emit a shell
    self-extracting file labeled ``.AppImage`` (audit H63-N036).
    """
    rpm_arch = normalize_rpm_arch(arch)
    out_name = f"{PACKAGE_NAME}-{version}-{rpm_arch}.AppImage"
    out_path = dist / out_name
    files = required_packaging_files(linux_installer)

    if dry_run:
        return out_path

    dist.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="winos-appdir-") as tmp:
        appdir = Path(tmp) / f"{PACKAGE_NAME}.AppDir"
        usr_bin = appdir / "usr" / "bin"
        usr_share = appdir / "usr" / "share" / "applications"
        usr_bin.mkdir(parents=True)
        usr_share.mkdir(parents=True)

        _copy_binary(binary, usr_bin / "winos-api")
        apprun = render_template(
            files["appimage_apprun"].read_text(encoding="utf-8"),
            version=version,
            arch=rpm_arch,
        )
        (appdir / "AppRun").write_text(apprun, encoding="utf-8")
        (appdir / "AppRun").chmod(0o755)
        desktop = render_template(
            files["appimage_desktop"].read_text(encoding="utf-8"),
            version=version,
            arch=rpm_arch,
        )
        (appdir / f"{PACKAGE_NAME}.desktop").write_text(desktop, encoding="utf-8")
        (usr_share / f"{PACKAGE_NAME}.desktop").write_text(desktop, encoding="utf-8")
        (appdir / "VERSION").write_text(version + "\n", encoding="utf-8")
        linux_md = linux_installer / "LINUX.md"
        if linux_md.is_file():
            shutil.copy2(linux_md, appdir / "LINUX.md")
        # Minimal PNG-less icon placeholder (desktop Icon=winos-api)
        (appdir / "winos-api.png").write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\0" * 8
        )  # truncated marker — structural only

        tool = shutil.which("appimagetool")
        if not tool:
            raise PackagingError(
                "appimagetool not found on PATH; refusing shell .AppImage fallback (N036)"
            )
        if out_path.exists():
            out_path.unlink()
        r = subprocess.run(
            [tool, str(appdir), str(out_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0 or not out_path.is_file():
            err = (r.stderr or r.stdout or "").strip()
            raise PackagingError(
                "appimagetool failed (exit "
                f"{r.returncode}); refusing shell .AppImage fallback (N036)"
                + (f": {err[:400]}" if err else "")
            )
        head = out_path.read_bytes()[:4]
        if head.startswith(b"#!"):
            out_path.unlink(missing_ok=True)
            raise PackagingError(
                "appimagetool produced a shell script; refusing non-ELF .AppImage (N036)"
            )
        out_path.chmod(out_path.stat().st_mode | 0o111)
        return out_path



def package_format_summary(paths: list[Path]) -> dict[str, Any]:
    return {
        "artifacts": [str(p) for p in paths],
        "sizes": {p.name: p.stat().st_size for p in paths if p.is_file()},
        "formats": sorted({p.suffix.lstrip(".") or p.name.split(".")[-1] for p in paths}),
    }
