#!/usr/bin/env python3
"""N037 — Optional Authenticode + always write RELEASE_ATTESTATION.json.

Without cert secrets, PE stays unsigned; attestation still written.
With WINOS_RELEASE_SIGNING_KEY, non-authenticode artifacts are marked attested.
Exit 0 unless --require-signature. Never fake authenticode. No cert purchase.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from windows_os_api.installer.release_attestation import (  # noqa: E402
    ATTESTATION_FILENAME,
    ReleaseAttestationError,
    artifact_entry,
    attach_signature,
    build_attestation,
    collect_release_artifacts,
    default_publisher,
    load_release_private_key,
    pe_has_authenticode_blob,
    try_signtool_verify,
    write_attestation,
)

DIST = ROOT / "dist"
OUTPUT = ROOT / "installer" / "output"


def _read_package_version() -> str:
    import re

    pyproject = ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return "0.0.0"
    text = pyproject.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return m.group(1) if m else "0.0.0"


def _cert_available() -> tuple[bool, str]:
    pfx_path = os.environ.get("WINOS_SIGN_PFX_PATH", "").strip()
    pfx_b64 = os.environ.get("WINOS_SIGN_PFX_B64", "").strip()
    password = os.environ.get("WINOS_SIGN_PFX_PASSWORD")
    if pfx_path and Path(pfx_path).is_file():
        return True, "pfx_path"
    if pfx_b64 and password is not None:
        return True, "pfx_b64"
    return False, "missing"


def _materialize_pfx(tmpdir: Path) -> tuple[Path, str]:
    pfx_path = os.environ.get("WINOS_SIGN_PFX_PATH", "").strip()
    password = os.environ.get("WINOS_SIGN_PFX_PASSWORD", "")
    if pfx_path and Path(pfx_path).is_file():
        return Path(pfx_path), password
    b64 = os.environ.get("WINOS_SIGN_PFX_B64", "").strip()
    if not b64:
        raise ReleaseAttestationError("no PFX material")
    out = tmpdir / "code_sign.pfx"
    out.write_bytes(base64.b64decode(b64))
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass
    return out, password


def _find_signtool() -> str | None:
    env = os.environ.get("WINOS_SIGNTOOL", "").strip()
    if env and Path(env).is_file():
        return env
    import shutil

    return shutil.which("signtool")


def sign_pe(path: Path, *, pfx: Path, password: str, timestamp_url: str | None) -> dict:
    signtool = _find_signtool()
    if not signtool:
        raise ReleaseAttestationError("signtool not found (set WINOS_SIGNTOOL)")
    cmd = [signtool, "sign", "/f", str(pfx), "/p", password, "/fd", "SHA256", "/td", "SHA256"]
    if timestamp_url:
        cmd.extend(["/tr", timestamp_url])
    cmd.append(str(path))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    if proc.returncode != 0:
        raise ReleaseAttestationError(
            f"signtool sign failed for {path.name}: {(proc.stderr or proc.stdout)[-800:]}"
        )
    v = try_signtool_verify(path)
    subject = "signtool-verified" if v.get("ok") else None
    if not subject and pe_has_authenticode_blob(path):
        subject = "pe-signature-present"
    return {
        "signing_status": "authenticode",
        "signer_subject": subject or "signtool-signed",
        "signer_thumbprint": None,
        "authenticode_verified": bool(v.get("ok")),
        "pe_sig_present": bool(pe_has_authenticode_blob(path)),
    }


def list_default_artifacts(extra: list[Path] | None = None) -> list[Path]:
    found = collect_release_artifacts([DIST, OUTPUT])
    seen = {p.resolve() for p in found}
    if extra:
        for p in extra:
            p = Path(p)
            if p.is_file() and p.resolve() not in seen:
                found.append(p)
                seen.add(p.resolve())
    return found


def run(
    *,
    artifacts: list[Path] | None,
    out: Path | None,
    require_signature: bool,
    version: str | None,
    publisher: str | None,
    dry_run: bool,
) -> int:
    paths = artifacts if artifacts else list_default_artifacts()
    if not paths:
        print("N037: no artifacts under dist/ or installer/output/", file=sys.stderr)
        return 1 if require_signature else 0

    cert_ok, cert_how = _cert_available()
    ts = os.environ.get("WINOS_SIGN_TIMESTAMP_URL", "").strip() or None
    raw_entries: list[tuple] = []
    signed_any = False
    unsigned_exe = False

    with tempfile.TemporaryDirectory(prefix="winos-sign-") as td:
        pfx_info = None
        if cert_ok and not dry_run:
            try:
                pfx_info = _materialize_pfx(Path(td))
            except Exception as exc:  # noqa: BLE001
                print(f"N037: PFX materialize failed: {exc}", file=sys.stderr)
                if require_signature:
                    return 1
                pfx_info = None

        for path in paths:
            status = "unsigned"
            subject = None
            thumb = None
            extra_ev: dict = {}
            if path.suffix.lower() == ".exe" and pfx_info is not None:
                try:
                    ev = sign_pe(path, pfx=pfx_info[0], password=pfx_info[1], timestamp_url=ts)
                    status = ev["signing_status"]
                    subject = ev.get("signer_subject")
                    thumb = ev.get("signer_thumbprint")
                    extra_ev = {
                        k: ev[k]
                        for k in ("authenticode_verified", "pe_sig_present")
                        if k in ev and ev[k] is not None
                    }
                    signed_any = True
                    print(f"SIGNED {path}")
                except ReleaseAttestationError as exc:
                    print(f"SIGN FAIL {path}: {exc}", file=sys.stderr)
                    if require_signature:
                        return 1
                    unsigned_exe = True
            elif path.suffix.lower() == ".exe":
                unsigned_exe = True
                if dry_run and cert_ok:
                    print(f"DRY-RUN would sign {path}")
            raw_entries.append((path, status, subject, thumb, extra_ev))

    try:
        priv = load_release_private_key()
    except ReleaseAttestationError as exc:
        print(f"N037: release key load error: {exc}", file=sys.stderr)
        return 1

    entries = []
    for path, status, subject, thumb, extra_ev in raw_entries:
        if status == "unsigned" and priv is not None:
            status = "attested"
        entry = artifact_entry(
            path,
            signing_status=status,
            signer_subject=subject,
            signer_thumbprint=thumb,
            root=path.parent,
        )
        entry.update(extra_ev)
        entries.append(entry)

    if require_signature and not signed_any:
        print(
            f"N037: --require-signature but no Authenticode applied (cert={cert_how})",
            file=sys.stderr,
        )
        return 1

    pub = (publisher or default_publisher()).strip()
    ver = (version or _read_package_version()).strip()
    try:
        doc = build_attestation(version=ver, publisher=pub, artifacts=entries)
    except ReleaseAttestationError as exc:
        print(f"ATTEST FAIL: {exc}", file=sys.stderr)
        return 1

    att_path = Path(out) if out else paths[0].parent / ATTESTATION_FILENAME

    if dry_run:
        preview = attach_signature(doc, priv)
        print(json.dumps(preview, indent=2, sort_keys=True))
        print(f"DRY-RUN would write {att_path}")
        return 0

    write_attestation(doc, att_path, private_key=priv)
    for folder in {p.parent for p in paths}:
        dest = folder / ATTESTATION_FILENAME
        if dest.resolve() != att_path.resolve():
            write_attestation(doc, dest, private_key=priv)
            print(f"Wrote {dest}")
    print(
        f"Wrote {att_path} (authenticode_files={signed_any}, "
        f"unsigned_exe={unsigned_exe}, cert={cert_how})"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="N037 optional Authenticode + release attestation")
    p.add_argument("files", nargs="*", help="Explicit artifact paths")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--require-signature", action="store_true")
    p.add_argument("--version", default=None)
    p.add_argument("--publisher", default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    arts = [Path(f) for f in args.files] if args.files else None
    try:
        return run(
            artifacts=arts,
            out=Path(args.output) if args.output else None,
            require_signature=args.require_signature,
            version=args.version,
            publisher=args.publisher,
            dry_run=args.dry_run,
        )
    except ReleaseAttestationError as exc:
        print(f"N037 FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
