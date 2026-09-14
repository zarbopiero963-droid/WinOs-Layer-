"""N001 — forensic traceability: file existence must not equal DONE/PASS.

H63-N001 / #67: requisito senza evidenza resta aperto; dati corrotti non
diventano PASS. Le tre famiglie di stati e lo schema evidence sono in
docs/roadmap_traceability.md.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "forensic_audit.py"


def _load_audit_module():
    spec = importlib.util.spec_from_file_location("forensic_audit", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def audit_mod():
    return _load_audit_module()


def test_done_without_evidence_is_rejected(audit_mod, tmp_path: Path):
    """Requisito marcato DONE senza evidence non può passare (resta aperto)."""
    (tmp_path / "f.py").write_text("# stub\n", encoding="utf-8")
    (tmp_path / "t.py").write_text("# stub\n", encoding="utf-8")
    data = [
        {
            "pr": 1,
            "status": "DONE",
            "title": "no evidence",
            "files": ["f.py"],
            "tests": ["t.py"],
            "remaining": None,
        }
    ]
    errors = audit_mod.audit_entries(data, root=tmp_path)
    assert errors, "DONE senza evidence deve produrre errori"
    assert any("evidence" in e.lower() for e in errors)


def test_corrupted_status_does_not_pass(audit_mod, tmp_path: Path):
    """Status non nello schema (es. PASS) non deve far risultare OK."""
    (tmp_path / "f.py").write_text("# stub\n", encoding="utf-8")
    data = [
        {
            "pr": 2,
            "status": "PASS",
            "title": "corrupted",
            "files": ["f.py"],
            "tests": ["f.py"],
            "remaining": None,
        }
    ]
    errors = audit_mod.audit_entries(data, root=tmp_path)
    assert errors, "status corrotto/non ammessso deve fallire"
    assert any("status" in e.lower() or "PASS" in e for e in errors)


def test_done_with_evidence_and_files_passes(audit_mod, tmp_path: Path):
    (tmp_path / "f.py").write_text("# stub\n", encoding="utf-8")
    (tmp_path / "t.py").write_text("# stub\n", encoding="utf-8")
    data = [
        {
            "pr": 3,
            "status": "DONE",
            "title": "with evidence",
            "files": ["f.py"],
            "tests": ["t.py"],
            "remaining": None,
            "evidence": "unit probe EXIT=0; SHA example; limits: no desktop",
        }
    ]
    errors = audit_mod.audit_entries(data, root=tmp_path)
    assert errors == []


def test_partial_without_evidence_ok_if_files_exist(audit_mod, tmp_path: Path):
    """PARTIAL = ancora aperto: evidence non obbligatoria, file elencati sì."""
    (tmp_path / "f.py").write_text("# stub\n", encoding="utf-8")
    data = [
        {
            "pr": 4,
            "status": "PARTIAL",
            "title": "open lot",
            "files": ["f.py"],
            "tests": [],
            "remaining": "evidence chain for CLOSED still missing (N001)",
        }
    ]
    errors = audit_mod.audit_entries(data, root=tmp_path)
    assert errors == []


def test_roadmap_traceability_doc_exists():
    doc = ROOT / "docs" / "roadmap_traceability.md"
    assert doc.is_file(), "N001 must ship docs/roadmap_traceability.md"
    text = doc.read_text(encoding="utf-8")
    for needle in (
        "tre famiglie",
        "NOT_STARTED",
        "PARTIAL",
        "DONE",
        "PASS",
        "BLOCKED_ENV",
        "VERIFIED",
        "evidence",
        "CLOSED",
    ):
        assert needle in text, f"missing formalization marker: {needle}"


def test_repo_forensic_json_has_no_done_without_evidence():
    """Current-head registry must not claim DONE without evidence."""
    data = json.loads((ROOT / "docs" / "forensic_audit.json").read_text(encoding="utf-8"))
    bad = [
        e["pr"]
        for e in data
        if e.get("status") == "DONE" and not str(e.get("evidence") or "").strip()
    ]
    assert bad == [], f"DONE without evidence still present for PRs: {bad}"
