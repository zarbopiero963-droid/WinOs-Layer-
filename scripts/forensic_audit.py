#!/usr/bin/env python3
"""Forensic audit — fail if DONE claims lack files/tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT_JSON = ROOT / "docs" / "forensic_audit.json"
AUDIT_MD = ROOT / "docs" / "FORENSIC_AUDIT.md"


def exists_ok(rel: str) -> bool:
    p = ROOT / rel
    if p.exists():
        return True
    # Allow directory prefixes ending with /
    if rel.endswith("/"):
        return p.exists()
    # Allow directory path without trailing slash
    return p.is_dir()


def main() -> int:
    if not AUDIT_JSON.exists():
        print(f"FAIL: missing {AUDIT_JSON}")
        return 1
    if not AUDIT_MD.exists():
        print(f"FAIL: missing {AUDIT_MD}")
        return 1

    data = json.loads(AUDIT_JSON.read_text(encoding="utf-8"))
    errors: list[str] = []
    counts = {"DONE": 0, "PARTIAL": 0, "NOT_STARTED": 0}

    for entry in data:
        pr = entry["pr"]
        status = entry["status"]
        counts[status] = counts.get(status, 0) + 1
        files = entry.get("files") or []
        tests = entry.get("tests") or []

        if status == "DONE":
            if not files:
                errors.append(f"PR{pr}: DONE but no files listed")
            if not tests:
                errors.append(f"PR{pr}: DONE but no tests listed")
            for f in files:
                if not exists_ok(f):
                    errors.append(f"PR{pr}: DONE missing file: {f}")
            for t in tests:
                if not exists_ok(t):
                    errors.append(f"PR{pr}: DONE missing test: {t}")

        if status == "PARTIAL":
            if not files:
                errors.append(f"PR{pr}: PARTIAL but no files listed")
            for f in files:
                if not exists_ok(f):
                    errors.append(f"PR{pr}: PARTIAL missing listed file: {f}")
            if not entry.get("remaining"):
                # remaining may be only in MD; JSON has remaining field
                pass

        if status == "NOT_STARTED":
            # nothing required
            pass

    print("=== Forensic Audit ===")
    print(f"PRs: {len(data)}")
    print(f"DONE={counts.get('DONE',0)} PARTIAL={counts.get('PARTIAL',0)} NOT_STARTED={counts.get('NOT_STARTED',0)}")
    if errors:
        print(f"FAIL ({len(errors)} issues):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("OK: all DONE/PARTIAL claims have required files/tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
