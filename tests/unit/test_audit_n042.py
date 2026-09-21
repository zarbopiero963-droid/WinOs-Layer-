"""N042 / H63-N042 — Correlated, redacted, anti-tamper audit (B-OBS).

Unit-level on box. Full installed W/L H63-N042 is MANUAL_ONLY (#21).
Coverage themes: R03 R49 W005 L005 G09 G13 G18 / Q11 Q15.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from windows_os_api.core.security.audit import (
    DEFAULT_INTEGRITY_SECRET,
    AuditIntegrityError,
    AuditLogger,
    AuditParamError,
    AuditUnavailableError,
    IntegrityReport,
    redact_audit_value,
    reset_audit_logger,
    sanitize_audit_string,
)


@pytest.fixture()
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


@pytest.fixture()
def logger(audit_path: Path) -> AuditLogger:
    return AuditLogger(
        audit_path,
        integrity_secret=DEFAULT_INTEGRITY_SECRET,
        max_bytes=1_000_000,
        max_entries=10_000,
    )


def test_ids_present_and_unique(logger: AuditLogger):
    a = logger.log("n042.a", subject="s1", resource="r1")
    b = logger.log("n042.b", subject="s1", resource="r2")
    assert a["request_id"] and a["execution_id"]
    assert b["request_id"] and b["execution_id"]
    assert a["request_id"] != b["request_id"]
    assert a["execution_id"] != b["execution_id"]
    assert a["hmac"] and a["prev_hash"] == "0" * 64
    assert b["prev_hash"] == a["hmac"]


def test_redaction_before_persist(logger: AuditLogger, audit_path: Path):
    secret = "sk-audit-n042-DO-NOT-LEAK-999"
    logger.log(
        "n042.secret",
        detail={"api_key": secret, "password": "p@ss", "ok": True, "note": f"Bearer {secret}"},
    )
    raw = audit_path.read_text(encoding="utf-8")
    assert secret not in raw
    assert "p@ss" not in raw
    assert "api_key" not in raw  # key dropped
    assert "password" not in raw
    entry = logger.read_all()[0]
    assert entry["detail"]["ok"] is True
    assert secret not in json.dumps(entry)


def test_corruption_detected_fail_closed(logger: AuditLogger, audit_path: Path):
    logger.log("n042.ok", detail={"x": 1})
    logger.log("n042.ok2", detail={"x": 2})
    text = audit_path.read_text(encoding="utf-8")
    lines = text.splitlines(True)
    # Alter middle of first record
    lines[0] = lines[0].replace('"x":1', '"x":999')
    audit_path.write_text("".join(lines), encoding="utf-8")
    report = logger.verify_integrity()
    assert report.ok is False
    assert "hmac_mismatch" in report.reason or "chain_break" in report.reason
    with pytest.raises(AuditIntegrityError):
        logger.read_all()
    with pytest.raises(AuditIntegrityError):
        logger.read_page(limit=10, offset=0)


def test_injected_line_detected(logger: AuditLogger, audit_path: Path):
    logger.log("n042.base")
    forged = {
        "ts": "2099-01-01T00:00:00+00:00",
        "action": "admin.granted",
        "subject": "attacker",
        "resource": "",
        "outcome": "success",
        "detail": {},
        "request_id": "forged",
        "execution_id": "forged",
        "prev_hash": "0" * 64,
        "hmac": "a" * 64,
    }
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(forged) + "\n")
    report = logger.verify_integrity()
    assert report.ok is False


def test_log_injection_sanitized(logger: AuditLogger, audit_path: Path):
    evil = "legit\n{\"action\":\"injected\"}\r\n"
    entry = logger.log(evil, subject="a\nb", resource="r\x00x", detail={"msg": "line1\nline2"})
    assert "\n" not in entry["action"]
    assert "\r" not in entry["action"]
    assert "\n" not in entry["subject"]
    raw = audit_path.read_text(encoding="utf-8")
    # Exactly one JSONL record (plus final newline)
    assert raw.count("\n") == 1
    assert logger.verify_integrity().ok is True


def test_sanitize_control_chars():
    assert "\n" not in sanitize_audit_string("a\nb\rc")
    assert "\x00" not in sanitize_audit_string("x\x00y")


def test_pagination_clamp_and_stable(logger: AuditLogger):
    ids = []
    for i in range(5):
        e = logger.log(f"n042.page.{i}", detail={"i": i})
        ids.append(e["execution_id"])
    page0 = logger.read_page(limit=2, offset=0)
    page1 = logger.read_page(limit=2, offset=2)
    page2 = logger.read_page(limit=2, offset=4)
    assert [e["execution_id"] for e in page0["entries"]] == ids[0:2]
    assert [e["execution_id"] for e in page1["entries"]] == ids[2:4]
    assert [e["execution_id"] for e in page2["entries"]] == ids[4:5]
    seen = set()
    for p in (page0, page1, page2):
        for e in p["entries"]:
            assert e["execution_id"] not in seen
            seen.add(e["execution_id"])
    with pytest.raises(AuditParamError):
        logger.read_page(limit=-1, offset=0)
    with pytest.raises(AuditParamError):
        logger.read_page(limit=10, offset=-5)
    with pytest.raises(AuditParamError):
        logger.read_page(limit=10_000, offset=0)


def test_rotation_by_max_entries(tmp_path: Path):
    path = tmp_path / "rot.jsonl"
    log = AuditLogger(path, max_entries=2, max_bytes=10_000_000)
    log.log("a")
    log.log("b")
    assert path.exists()
    log.log("c")  # triggers rotate before/around third write
    rotated = path.with_suffix(path.suffix + ".1")
    assert rotated.exists()
    # Active file holds post-rotation entries
    assert log.verify_integrity().ok is True
    assert len(log.read_all()) >= 1


def test_unavailable_path_fail_closed(tmp_path: Path):
    # File path whose parent is a file → mkdir/write fails
    blocker = tmp_path / "notadir"
    blocker.write_text("x", encoding="utf-8")
    bad = blocker / "audit.jsonl"
    log = AuditLogger(bad, create_parent=True)
    with pytest.raises(AuditUnavailableError):
        log.log("n042.unavailable")
    report = log.verify_integrity()
    assert report.ok is False
    assert report.available is False
    # Error message must not contain secrets
    assert "sk-" not in str(report.reason)


def test_recovery_error_reports_have_no_secrets(logger: AuditLogger, audit_path: Path):
    logger.log("n042.x", detail={"token": "sk-hidden-value-ABCDEF"})
    raw = audit_path.read_text(encoding="utf-8")
    # Corrupt
    audit_path.write_text(raw.replace("n042.x", "n042.Y"), encoding="utf-8")
    report = logger.verify_integrity()
    blob = json.dumps({"reason": report.reason, "ok": report.ok})
    assert "sk-hidden" not in blob
    assert "ABCDEF" not in blob


def test_redact_audit_value_helper():
    out = redact_audit_value({"api_key": "sk-abc", "nested": {"password": "x"}, "keep": 1})
    assert "api_key" not in out
    assert "password" not in out["nested"]
    assert out["keep"] == 1


def test_acl_audit_admin_only(client, auth_headers, admin_headers, tmp_sandbox):
    # Seed an entry via admin-only path side effect / direct logger
    from windows_os_api.core.security.audit import get_audit_logger

    get_audit_logger().log("n042.acl", detail={"api_key": "sk-should-not-leak-ACL"})
    denied = client.get("/v1/audit", headers=auth_headers)
    assert denied.status_code == 403
    assert "sk-should-not-leak" not in denied.text
    assert "entries" not in (denied.json() if denied.headers.get("content-type", "").startswith("application/json") else {})
    ok = client.get("/v1/audit", headers=admin_headers, params={"limit": 50, "offset": 0})
    assert ok.status_code == 200
    body = ok.json()
    assert "entries" in body
    assert body.get("integrity") == "ok"
    assert "sk-should-not-leak" not in json.dumps(body)


def test_api_pagination_rejects_bad_params(client, admin_headers, tmp_sandbox):
    assert client.get("/v1/audit", headers=admin_headers, params={"limit": -1}).status_code == 400
    assert client.get("/v1/audit", headers=admin_headers, params={"offset": -1}).status_code == 400
    assert client.get("/v1/audit", headers=admin_headers, params={"limit": 99999}).status_code == 400


def test_api_integrity_failure_fail_closed(client, admin_headers, tmp_sandbox, monkeypatch):
    from windows_os_api.core.security.audit import get_audit_logger

    logger = get_audit_logger()
    logger.log("n042.seed")
    # Corrupt file on disk
    path = logger.path
    path.write_text(path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    r = client.get("/v1/audit", headers=admin_headers)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "audit_integrity_failure"
    assert "sk-" not in json.dumps(detail)


def test_cli_verify_and_tail(tmp_path: Path, monkeypatch):
    from windows_os_api.cli.main import main
    from windows_os_api.core.runtime.config import get_settings

    path = tmp_path / "cli-audit.jsonl"
    monkeypatch.setenv("WINOS_AUDIT_LOG_PATH", str(path))
    monkeypatch.setenv("WINOS_AUDIT_INTEGRITY_SECRET", DEFAULT_INTEGRITY_SECRET)
    get_settings.cache_clear()
    reset_audit_logger()
    log = AuditLogger(path, integrity_secret=DEFAULT_INTEGRITY_SECRET)
    log.log("n042.cli", detail={"api_key": "sk-cli-secret-XXXX"})
    reset_audit_logger()
    assert main(["audit", "verify"]) == 0
    # Corrupt → non-zero
    path.write_text(path.read_text(encoding="utf-8").replace("n042.cli", "TAMPER"), encoding="utf-8")
    reset_audit_logger()
    assert main(["audit", "verify"]) == 2


def test_concurrent_writes_preserve_chain(tmp_path: Path):
    import threading

    path = tmp_path / "conc.jsonl"
    log = AuditLogger(path, integrity_secret=DEFAULT_INTEGRITY_SECRET, max_entries=10_000)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(20):
                log.log(f"n042.conc.{n}.{i}", detail={"n": n, "i": i})
        except BaseException as exc:  # noqa: BLE001 — collect for assert
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    report = log.verify_integrity()
    assert report.ok is True
    assert report.entries_checked == 80


def test_n042_correlation_context_reused_across_logs(tmp_path: Path):
    from windows_os_api.core.security.audit import (
        bind_correlation,
        clear_correlation,
        AuditLogger,
    )

    path = tmp_path / "corr.jsonl"
    log = AuditLogger(path, integrity_secret=DEFAULT_INTEGRITY_SECRET)
    bind_correlation(request_id="req-shared", execution_id="exec-shared")
    try:
        a = log.log("n042.corr.a", subject="s")
        b = log.log("n042.corr.b", subject="s")
    finally:
        clear_correlation()
    assert a["request_id"] == b["request_id"] == "req-shared"
    assert a["execution_id"] == b["execution_id"] == "exec-shared"
    c = log.log("n042.corr.c", subject="s")
    assert c["request_id"] != "req-shared"
