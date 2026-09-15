"""N044 / H63-N044 — Protected diagnose / support-bundle collection (B-OBS).

Unit-level on box. Full installed W/L H63-N044 is MANUAL_ONLY (#21).
Coverage themes: R49 W030 W095 L030 L095 G18 / Q11 Q12.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from windows_os_api.cli import main as cli_main
from windows_os_api.core.security.audit import (
    DEFAULT_INTEGRITY_SECRET,
    AuditLogger,
    reset_audit_logger,
)
from windows_os_api.observability.diagnose import (
    DiagnoseParamError,
    build_support_bundle,
    clamp_max_bytes,
    collect_before_restart,
    default_bundle_path,
    redact_config,
    write_support_bundle,
)
from windows_os_api.observability.metrics import get_metrics, reset_metrics
from windows_os_api.os.runtime_health import probe_liveness


SECRET = "sk-n044-DO-NOT-LEAK-SUPPORT-BUNDLE"
ADMIN_HEADERS = {"X-API-Key": "admin-key-change-me"}
VIEWER_HEADERS = {"X-API-Key": "viewer-key-n011"}


@pytest.fixture(autouse=True)
def _reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("WINOS_AUDIT_LOG_PATH", str(audit))
    monkeypatch.setenv("WINOS_AUDIT_INTEGRITY_SECRET", DEFAULT_INTEGRITY_SECRET)
    monkeypatch.setenv("WINOS_ADMIN_API_KEYS", json.dumps(["admin-key-change-me"]))
    monkeypatch.setenv("WINOS_API_KEYS", json.dumps(["dev-key-change-me", SECRET]))
    monkeypatch.setenv("WINOS_AI_API_KEY", SECRET)
    reset_audit_logger()
    reset_metrics()
    yield
    reset_audit_logger()
    reset_metrics()


def test_redact_config_drops_api_keys_and_secrets():
    cfg = redact_config(
        {
            "host": "127.0.0.1",
            "port": 8787,
            "api_keys": [SECRET, "other"],
            "admin_api_keys": [SECRET],
            "audit_integrity_secret": DEFAULT_INTEGRITY_SECRET,
            "ai_api_key": SECRET,
            "require_auth": True,
        }
    )
    assert cfg["host"] == "127.0.0.1"
    assert cfg["port"] == 8787
    assert cfg["require_auth"] is True
    assert cfg["api_keys"]["redacted"] is True
    assert cfg["api_keys"]["count"] == 2
    assert SECRET not in json.dumps(cfg)
    assert DEFAULT_INTEGRITY_SECRET not in json.dumps(cfg)
    assert "admin_api_keys" in cfg and cfg["admin_api_keys"]["redacted"] is True


def test_build_bundle_contains_stack_process_config_no_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WINOS_AUDIT_LOG_PATH", str(tmp_path / "a.jsonl"))
    reset_audit_logger()
    logger = AuditLogger(
        tmp_path / "a.jsonl",
        integrity_secret=DEFAULT_INTEGRITY_SECRET,
    )
    logger.log("n044.seed", detail={"api_key": SECRET, "ok": True})

    # Point singleton at our logger path via env already set in fixture;
    # rebuild bundle after logging through get_audit_logger.
    reset_audit_logger()
    from windows_os_api.core.security.audit import get_audit_logger

    get_audit_logger().log("n044.seed2", detail={"password": "p@ss", "note": "hi"})

    bundle = build_support_bundle(reason="unit", before_restart=False)
    assert bundle["schema"] == "winos.support_bundle.v1"
    assert bundle["bundle_id"]
    assert bundle["request_id"] and bundle["execution_id"]
    assert "process" in bundle and bundle["process"]["pid"] == os.getpid()
    assert "stacks" in bundle and isinstance(bundle["stacks"], list)
    assert "config" in bundle
    raw = json.dumps(bundle)
    assert SECRET not in raw
    assert "p@ss" not in raw
    assert DEFAULT_INTEGRITY_SECRET not in raw
    assert "admin-key-change-me" not in raw
    # Correlated audit present (or integrity note) — never clear secrets.
    assert "audit" in bundle


def test_bundle_works_when_backend_blocked(monkeypatch):
    """Blocked runtime still yields a limited bundle (H63-N044)."""
    from windows_os_api.backends.factory import BackendUnavailable
    import windows_os_api.os.runtime_health as rh

    def boom(**_kwargs):
        raise BackendUnavailable("blocked for n044")

    monkeypatch.setattr(rh, "probe_runtime_health", boom)
    # build_support_bundle catches health probe failures inside capture_health_snapshot
    # via probe_runtime_health call — patch at module used by diagnose.
    import windows_os_api.observability.diagnose as diag

    def health_blocked():
        return {
            "live": {"live": True, "status": "alive"},
            "ready": {"ready": False, "error": "BackendUnavailable"},
        }

    monkeypatch.setattr(diag, "capture_health_snapshot", health_blocked)
    bundle = build_support_bundle(reason="blocked", max_bytes=64_000)
    assert bundle["health"]["live"]["live"] is True
    assert bundle["health"]["ready"]["ready"] is False
    assert bundle["process"]["pid"]
    assert len(json.dumps(bundle).encode()) <= 64_000 + 512  # size_bytes meta slack


def test_max_bytes_clamp_and_truncation(tmp_path: Path):
    with pytest.raises(DiagnoseParamError):
        clamp_max_bytes(100)
    with pytest.raises(DiagnoseParamError):
        clamp_max_bytes(9_000_000)
    # Tiny-but-valid cap should truncate heavy sections.
    bundle = build_support_bundle(reason="tiny", max_bytes=8_192)
    assert bundle["max_bytes"] == 8_192
    assert bundle["size_bytes"] <= 8_192 + 256
    raw = json.dumps(bundle).encode("utf-8")
    assert len(raw) <= 12_000  # soft bound for unit env


def test_write_bundle_mode_600_and_collect_before_restart(tmp_path: Path):
    path = tmp_path / "bundle.json"
    result = collect_before_restart(path, reason="pre-restart", max_bytes=128_000)
    assert result.before_restart is True
    assert path.is_file()
    if os.name == "posix":
        mode = stat.S_IMODE(path.stat().st_mode)
        # Owner rw only — group/other must be clear (ACL intent on Unix).
        assert mode & 0o077 == 0
        assert mode & 0o600 == 0o600
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["before_restart"] is True
    assert SECRET not in path.read_text(encoding="utf-8")
    assert get_metrics().snapshot(collect=False)["counters"].get("diagnose.bundles", 0) >= 1


def test_cli_diagnose_writes_bundle(tmp_path: Path):
    out = tmp_path / "cli-bundle.json"
    rc = cli_main.main(
        ["diagnose", "--output", str(out), "--before-restart", "--max-bytes", "131072"]
    )
    assert rc == 0
    assert out.is_file()
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["before_restart"] is True
    assert SECRET not in out.read_text(encoding="utf-8")


def test_cli_diagnose_rejects_bad_max_bytes(tmp_path: Path):
    out = tmp_path / "bad.json"
    rc = cli_main.main(["diagnose", "-o", str(out), "--max-bytes", "100"])
    assert rc == 2
    assert not out.exists()


def test_rest_admin_only_and_no_secrets(client, admin_headers, viewer_headers):
    denied = client.get("/v1/diagnose/bundle", headers=viewer_headers)
    assert denied.status_code in (401, 403)

    ok = client.get("/v1/diagnose/bundle", headers=admin_headers)
    assert ok.status_code == 200
    body = ok.json()
    assert body["schema"] == "winos.support_bundle.v1"
    dumped = json.dumps(body)
    assert SECRET not in dumped
    assert "admin-key-change-me" not in dumped
    assert "viewer-key-n011" not in dumped
    assert DEFAULT_INTEGRITY_SECRET not in dumped


def test_rest_collect_before_restart_then_live_recovers(client, admin_headers, tmp_path, monkeypatch):
    """Collect-before-restart semantics; service stays live (restart = new app in unit)."""
    out_dir = tmp_path / "bundles"
    out_dir.mkdir()
    # Force default_bundle_path into tmp via output_dir query/body — use query params.
    r = client.post(
        "/v1/diagnose/collect",
        headers=admin_headers,
        params={"before_restart": "true", "output_dir": str(out_dir), "max_bytes": 131072},
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["ok"] is True
    assert payload["before_restart"] is True
    path = Path(payload["path"])
    assert path.is_file()
    assert path.parent == out_dir
    assert SECRET not in path.read_text(encoding="utf-8")

    # "Restart recovers service": liveness still true after collect; recreate app.
    live = client.get("/v1/live")
    assert live.status_code == 200
    assert live.json()["live"] is True
    assert probe_liveness()["live"] is True


def test_default_bundle_path_under_logs():
    p = default_bundle_path()
    assert "support-bundle-" in p.name
    assert p.suffix == ".json"


def test_write_rejects_directory(tmp_path: Path):
    with pytest.raises(DiagnoseParamError):
        write_support_bundle(tmp_path)
