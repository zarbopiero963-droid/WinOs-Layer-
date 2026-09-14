#!/usr/bin/env python3
"""Forensic audit — fail if DONE claims lack files/tests/evidence.

N001 / #67 / #63: file existence alone must not equal DONE. A DONE row needs
non-empty evidence. Unknown/corrupt statuses fail closed (they must not PASS).
See docs/roadmap_traceability.md for the three status families and CLOSED chain.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AUDIT_JSON = ROOT / "docs" / "forensic_audit.json"
AUDIT_MD = ROOT / "docs" / "FORENSIC_AUDIT.md"

ALLOWED_STATUSES = frozenset({"DONE", "PARTIAL", "NOT_STARTED"})


def exists_ok(rel: str, root: Path) -> bool:
    p = root / rel
    if p.exists():
        return True
    if rel.endswith("/"):
        return p.exists()
    return p.is_dir()


def _evidence_ok(entry: dict[str, Any]) -> bool:
    ev = entry.get("evidence")
    if ev is None:
        return False
    if isinstance(ev, str):
        return bool(ev.strip())
    if isinstance(ev, (list, dict)):
        return bool(ev)
    return False


def audit_entries(data: list[dict[str, Any]], root: Path | None = None) -> list[str]:
    """Validate forensic registry entries. Returns error strings (empty => OK)."""
    base = root if root is not None else ROOT
    errors: list[str] = []

    if not isinstance(data, list):
        return ["forensic registry must be a JSON list"]

    for entry in data:
        if not isinstance(entry, dict):
            errors.append(f"malformed entry (not an object): {entry!r}")
            continue
        pr = entry.get("pr", "?")
        status = entry.get("status")
        files = entry.get("files") or []
        tests = entry.get("tests") or []

        if status not in ALLOWED_STATUSES:
            errors.append(
                f"PR{pr}: invalid/corrupt status {status!r} "
                f"(allowed: {sorted(ALLOWED_STATUSES)}); must not PASS"
            )
            continue

        if status == "DONE":
            if not files:
                errors.append(f"PR{pr}: DONE but no files listed")
            if not tests:
                errors.append(f"PR{pr}: DONE but no tests listed")
            if not _evidence_ok(entry):
                errors.append(
                    f"PR{pr}: DONE without evidence — requirement stays open "
                    f"(file existence is not DONE)"
                )
            for f in files:
                if not exists_ok(str(f), base):
                    errors.append(f"PR{pr}: DONE missing file: {f}")
            for t in tests:
                if not exists_ok(str(t), base):
                    errors.append(f"PR{pr}: DONE missing test: {t}")

        elif status == "PARTIAL":
            if not files:
                errors.append(f"PR{pr}: PARTIAL but no files listed")
            for f in files:
                if not exists_ok(str(f), base):
                    errors.append(f"PR{pr}: PARTIAL missing listed file: {f}")

        elif status == "NOT_STARTED":
            pass

    return errors


def main() -> int:
    if not AUDIT_JSON.exists():
        print(f"FAIL: missing {AUDIT_JSON}")
        return 1
    if not AUDIT_MD.exists():
        print(f"FAIL: missing {AUDIT_MD}")
        return 1

    try:
        data = json.loads(AUDIT_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"FAIL: corrupt forensic JSON: {exc}")
        return 1

    if not isinstance(data, list):
        print("FAIL: forensic JSON root must be a list")
        return 1

    counts = {"DONE": 0, "PARTIAL": 0, "NOT_STARTED": 0, "OTHER": 0}
    for entry in data:
        if not isinstance(entry, dict):
            counts["OTHER"] += 1
            continue
        st = entry.get("status")
        if st in counts:
            counts[st] += 1
        else:
            counts["OTHER"] += 1

    errors = audit_entries(data, root=ROOT)

    print("=== Forensic Audit ===")
    print(f"PRs: {len(data)}")
    print(
        f"DONE={counts['DONE']} PARTIAL={counts['PARTIAL']} "
        f"NOT_STARTED={counts['NOT_STARTED']} OTHER={counts['OTHER']}"
    )
    if errors:
        print(f"FAIL ({len(errors)} issues):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(
        "OK: DONE requires files+tests+evidence; "
        "PARTIAL/NOT_STARTED stay open; unknown status fails closed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
