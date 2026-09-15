#!/usr/bin/env python3
"""N037 — Independent verify for RELEASE_ATTESTATION.json + artifacts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from windows_os_api.installer.release_attestation import (  # noqa: E402
    ATTESTATION_FILENAME,
    ReleaseAttestationError,
    default_publisher,
    load_release_public_key,
    verify_release_artifacts,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="N037 verify release attestation + hashes")
    p.add_argument("attestation", nargs="?", default=None)
    p.add_argument("--root", default=None)
    p.add_argument("--publisher", default=None)
    p.add_argument("--require-attestation-signature", action="store_true")
    p.add_argument("--require-authenticode", action="store_true")
    args = p.parse_args(argv)

    att = Path(args.attestation) if args.attestation else None
    if att is None:
        for cand in (
            ROOT / "dist" / ATTESTATION_FILENAME,
            ROOT / "installer" / "output" / ATTESTATION_FILENAME,
        ):
            if cand.is_file():
                att = cand
                break
    if att is None or not att.is_file():
        print(f"VERIFY FAIL: {ATTESTATION_FILENAME} not found", file=sys.stderr)
        return 1

    root = Path(args.root) if args.root else att.parent
    expected = args.publisher if args.publisher is not None else default_publisher()
    try:
        pk = load_release_public_key()
    except ReleaseAttestationError as exc:
        print(f"VERIFY FAIL: {exc}", file=sys.stderr)
        return 1

    report = verify_release_artifacts(
        att,
        root=root,
        expected_publisher=expected,
        public_key=pk,
        require_attestation_signature=args.require_attestation_signature,
        require_authenticode=args.require_authenticode,
    )
    print(json.dumps(report, indent=2))
    if report.get("ok"):
        print("VERIFY-RELEASE OK")
        return 0
    print(f"VERIFY-RELEASE FAIL ({len(report.get('errors', []))} errors)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
