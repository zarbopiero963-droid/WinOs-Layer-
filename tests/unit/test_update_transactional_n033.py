"""H63-N033 — Update transazionale Linux (verify obbligatorio, permessi fail-closed, journal)."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from windows_os_api.update.manager import UpdateManager, UpdatePackage
from windows_os_api.update.checksum_manifest import sha256_file


def _snapshot(install: Path) -> dict[str, str]:
    """Relative path → content hash; exclude update_state.json and write probes."""
    out: dict[str, str] = {}
    for p in sorted(install.rglob("*")):
        if not p.is_file():
            continue
        if p.name in {"update_state.json", ".winos_update_write_probe"}:
            continue
        rel = p.relative_to(install).as_posix()
        out[rel] = sha256_file(p)
    return out


def _dir_package(tmp_path: Path, version: str, files: dict[str, bytes]) -> UpdatePackage:
    pkg = tmp_path / f"pkg-{version}"
    pkg.mkdir()
    for name, data in files.items():
        target = pkg / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manifest = UpdateManager.write_package_manifest(pkg)
    return UpdatePackage(
        version=version,
        path=pkg,
        checksum_sha256=sha256_file(manifest),
    )


def test_h63_n033_apply_rejects_verify_false(tmp_path: Path):
    install = tmp_path / "install"
    backup = tmp_path / "backup"
    install.mkdir()
    (install / "app.txt").write_text("v1", encoding="utf-8")
    mgr = UpdateManager(install, backup)
    src = tmp_path / "release.bin"
    src.write_bytes(b"NEW")
    pkg = UpdatePackage(version="2.0.0", path=src, checksum_sha256=sha256_file(src))
    blocked = mgr.apply(pkg, verify=False)
    assert blocked["ok"] is False
    assert "verify" in blocked.get("error", "").lower()
    assert not (install / "release.bin").exists()


def test_h63_n033_transactional_always_verifies(tmp_path: Path):
    install = tmp_path / "install"
    backup = tmp_path / "backup"
    install.mkdir()
    (install / "app.txt").write_text("vA", encoding="utf-8")
    mgr = UpdateManager(install, backup)
    pkg = _dir_package(tmp_path, "2.0.0", {"app.txt": b"vB", "new.so": b"SO"})
    (pkg.path / "app.txt").write_bytes(b"EVIL")
    result = mgr.apply_linux_transactional(pkg)
    assert result["ok"] is False
    assert "checksum" in result.get("error", "").lower() or "verif" in result.get("error", "").lower()
    assert result.get("stage") == "preflight"
    assert "journal" in result
    assert (install / "app.txt").read_text(encoding="utf-8") == "vA"
    assert not (install / "new.so").exists()


def test_h63_n033_success_stop_replace_start_health_journal(tmp_path: Path):
    install = tmp_path / "install"
    backup = tmp_path / "backup"
    install.mkdir()
    (install / "app.txt").write_text("vA", encoding="utf-8")
    (install / "only_in_a.cfg").write_text("keep-me-on-rollback", encoding="utf-8")
    mgr = UpdateManager(install, backup)
    before = _snapshot(install)
    pkg = _dir_package(tmp_path, "2.0.0", {"app.txt": b"vB", "new.so": b"SO"})

    stages: list[str] = []

    def stop() -> dict[str, Any]:
        stages.append("stop")
        return {"ok": True}

    def start() -> dict[str, Any]:
        stages.append("start")
        return {"ok": True}

    def health() -> dict[str, Any]:
        stages.append("health")
        return {"ok": True, "status": "healthy"}

    result = mgr.apply_linux_transactional(
        pkg, stop_service=stop, start_service=start, health_check=health
    )
    assert result["ok"] is True
    assert result.get("platform") == "linux"
    assert stages == ["stop", "start", "health"]
    assert result.get("stage") == "done"
    assert "preflight" in result.get("stages", [])
    assert "permissions" in result.get("stages", [])
    assert "backup" in result.get("stages", [])
    assert "done" in result.get("stages", [])
    journal = result.get("journal")
    assert isinstance(journal, list) and journal
    assert any("preflight" in j for j in journal)
    assert any("permissions" in j for j in journal)
    assert journal[-1] == "done" or any("done" in j for j in journal)
    assert (install / "app.txt").read_bytes() == b"vB"
    assert (install / "new.so").read_bytes() == b"SO"
    assert not (install / "only_in_a.cfg").exists()
    assert mgr.version == "2.0.0"
    assert before["app.txt"] != _snapshot(install)["app.txt"]


@pytest.mark.parametrize(
    "fail_stage",
    ["stop", "replace", "start", "health"],
)
def test_h63_n033_failure_at_stage_rolls_back_no_orphans(
    tmp_path: Path, fail_stage: str, monkeypatch: pytest.MonkeyPatch
):
    install = tmp_path / "install"
    backup = tmp_path / "backup"
    install.mkdir()
    (install / "app.txt").write_text("vA", encoding="utf-8")
    (install / "only_in_a.cfg").write_text("preserve", encoding="utf-8")
    mgr = UpdateManager(install, backup)
    expected = _snapshot(install)
    pkg = _dir_package(tmp_path, "2.0.0", {"app.txt": b"vB", "new.so": b"ORPHAN-SO"})

    def stop() -> dict[str, Any]:
        if fail_stage == "stop":
            return {"ok": False, "error": "systemctl stop failed"}
        return {"ok": True}

    def start() -> dict[str, Any]:
        if fail_stage == "start":
            return {"ok": False, "error": "systemctl start failed"}
        return {"ok": True}

    def health() -> dict[str, Any]:
        if fail_stage == "health":
            return {"ok": False, "error": "health failed", "status": "unhealthy"}
        return {"ok": True, "status": "ok"}

    if fail_stage == "replace":
        real_replace = mgr._replace_install_tree

        def boom(package: UpdatePackage) -> None:
            real_replace(package)
            raise RuntimeError("injected replace failure after partial copy")

        monkeypatch.setattr(mgr, "_replace_install_tree", boom)

    result = mgr.apply_linux_transactional(
        pkg, stop_service=stop, start_service=start, health_check=health
    )
    assert result["ok"] is False
    assert result.get("rolled_back") is True or fail_stage == "stop"
    assert "journal" in result
    assert _snapshot(install) == expected
    assert not (install / "new.so").exists()
    assert (install / "only_in_a.cfg").read_text(encoding="utf-8") == "preserve"
    assert mgr.version == "1.0.0"


def test_h63_n033_ambiguous_health_is_not_success(tmp_path: Path):
    install = tmp_path / "install"
    backup = tmp_path / "backup"
    install.mkdir()
    (install / "app.txt").write_text("vA", encoding="utf-8")
    mgr = UpdateManager(install, backup)
    expected = _snapshot(install)
    pkg = _dir_package(tmp_path, "2.0.0", {"app.txt": b"vB", "new.so": b"SO"})

    def health_ambiguous() -> dict[str, Any]:
        return {"message": "maybe fine"}

    result = mgr.apply_linux_transactional(
        pkg,
        stop_service=lambda: {"ok": True},
        start_service=lambda: {"ok": True},
        health_check=health_ambiguous,
    )
    assert result["ok"] is False
    assert "health" in result.get("error", "").lower() or "ambiguous" in result.get(
        "error", ""
    ).lower()
    assert result.get("rolled_back") is True
    assert "journal" in result
    assert _snapshot(install) == expected
    assert not (install / "new.so").exists()


def test_h63_n033_permission_denied_fail_closed_no_orphans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Permissions denied → fail closed before replace; no orphans; clear error.

    Portable: monkeypatch the writable preflight (Windows chmod on dirs is not
    equivalent to POSIX non-writable; production path still uses os.access +
    probe write).
    """
    install = tmp_path / "install"
    backup = tmp_path / "backup"
    install.mkdir()
    (install / "app.txt").write_text("vA", encoding="utf-8")
    mgr = UpdateManager(install, backup)
    expected = _snapshot(install)
    pkg = _dir_package(tmp_path, "2.0.0", {"app.txt": b"vB", "new.so": b"SO"})

    monkeypatch.setattr(
        mgr,
        "_install_dir_writable",
        lambda: (False, f"permissions denied: install_dir not writable: {install}"),
    )

    called_stop = {"n": 0}

    def stop() -> dict[str, Any]:
        called_stop["n"] += 1
        return {"ok": True}

    result = mgr.apply_linux_transactional(
        pkg,
        stop_service=stop,
        start_service=lambda: {"ok": True},
        health_check=lambda: {"ok": True, "status": "ok"},
    )

    assert result["ok"] is False
    assert result.get("stage") == "permissions"
    err = result.get("error", "").lower()
    assert "permission" in err or "writable" in err or "denied" in err
    assert result.get("rolled_back") is False
    assert called_stop["n"] == 0  # never reached stop/replace
    assert "journal" in result
    assert any("permissions" in j for j in result["journal"])
    assert _snapshot(install) == expected
    assert not (install / "new.so").exists()
    assert (install / "app.txt").read_text(encoding="utf-8") == "vA"
    assert mgr.version == "1.0.0"


@pytest.mark.skipif(os.name == "nt", reason="POSIX chmod non-writable dirs only")
def test_h63_n033_permission_denied_via_chmod_posix(tmp_path: Path):
    """Real POSIX: chmod install_dir not writable → fail closed, no orphans."""
    install = tmp_path / "install"
    backup = tmp_path / "backup"
    install.mkdir()
    (install / "app.txt").write_text("vA", encoding="utf-8")
    mgr = UpdateManager(install, backup)
    expected = _snapshot(install)
    pkg = _dir_package(tmp_path, "2.0.0", {"app.txt": b"vB", "new.so": b"SO"})

    mode_before = install.stat().st_mode
    install.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = mgr.apply_linux_transactional(
            pkg,
            stop_service=lambda: {"ok": True},
            start_service=lambda: {"ok": True},
            health_check=lambda: {"ok": True, "status": "ok"},
        )
    finally:
        install.chmod(mode_before)

    assert result["ok"] is False
    assert result.get("stage") == "permissions"
    assert result.get("rolled_back") is False
    assert _snapshot(install) == expected
    assert not (install / "new.so").exists()


def test_h63_n033_rollback_deletes_files_introduced_by_update(tmp_path: Path):
    install = tmp_path / "install"
    backup = tmp_path / "backup"
    install.mkdir()
    (install / "app.txt").write_text("vA", encoding="utf-8")
    mgr = UpdateManager(install, backup)
    expected = _snapshot(install)
    pkg = _dir_package(tmp_path, "2.0.0", {"app.txt": b"vB", "brand_new.bin": b"NEW"})

    assert mgr.verify(pkg) is True
    mgr.backup_current()
    mgr._replace_install_tree(pkg)
    assert (install / "brand_new.bin").exists()
    rb = mgr.rollback()
    assert rb["ok"] is True
    assert _snapshot(install) == expected
    assert not (install / "brand_new.bin").exists()


def test_h63_n033_missing_hooks_fail_closed(tmp_path: Path):
    install = tmp_path / "install"
    backup = tmp_path / "backup"
    install.mkdir()
    (install / "app.txt").write_text("vA", encoding="utf-8")
    mgr = UpdateManager(install, backup)
    pkg = _dir_package(tmp_path, "2.0.0", {"app.txt": b"vB"})
    result = mgr.apply_linux_transactional(pkg)
    assert result["ok"] is False
    assert "hooks missing" in result.get("error", "").lower()
    assert (install / "app.txt").read_text(encoding="utf-8") == "vA"


def test_h63_n033_hook_exception_rolls_back(tmp_path: Path):
    install = tmp_path / "install"
    backup = tmp_path / "backup"
    install.mkdir()
    (install / "app.txt").write_text("vA", encoding="utf-8")
    mgr = UpdateManager(install, backup)
    expected = _snapshot(install)
    pkg = _dir_package(tmp_path, "2.0.0", {"app.txt": b"vB", "new.bin": b"X"})

    def boom_start() -> dict[str, Any]:
        raise RuntimeError("SCM start exploded")

    result = mgr.apply_linux_transactional(
        pkg,
        stop_service=lambda: {"ok": True},
        start_service=boom_start,
        health_check=lambda: {"ok": True, "status": "ok"},
    )
    assert result["ok"] is False
    assert "raised" in result.get("error", "").lower() or "explod" in result.get("error", "").lower()
    assert result.get("rolled_back") is True
    assert _snapshot(install) == expected
