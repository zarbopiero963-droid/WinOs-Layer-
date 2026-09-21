"""Auto-update with verify / backup / rollback.

N028: directory packages verify via an embedded checksum manifest that never
lists itself; duplicate basenames, missing or altered files fail closed.

N031: HTTPS release discovery + verified staged download.

N032: transactional Windows apply — always verify on the distributed path;
stop → replace → start → health with injectable hooks; rollback deletes files
newly introduced by a failed/partial update (Phase 0 gap closed).

N033: transactional Linux apply — same state machine for systemd/user + Linux
install tree; permission preflight fail-closed; journal/stages trail; injectable
stop/start/health hooks (no live systemctl required in CI).
"""
from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from windows_os_api.update.checksum_manifest import (
    ChecksumManifestError,
    find_package_manifest,
    sha256_file,
    verify_checksum_manifest,
    write_checksum_manifest,
)
from windows_os_api.update.discovery import (
    ReleaseDiscoveryError,
    ReleaseInfo,
    discover_release,
    download_verified,
)

# Health statuses that count as success. Anything else (missing, unknown,
# degraded, unhealthy) is failure — ambiguous health is not success (N032).
_HEALTH_OK = frozenset({"ok", "healthy", "ready"})

Hook = Callable[[], Mapping[str, Any]]


@dataclass
class UpdatePackage:
    version: str
    path: Path
    checksum_sha256: str


class UpdateManager:
    def __init__(self, install_dir: str | Path, backup_dir: str | Path) -> None:
        self.install_dir = Path(install_dir)
        self.backup_dir = Path(backup_dir)
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.install_dir / "update_state.json"
        self._state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return {"version": "1.0.0", "backups": []}

    def _save_state(self) -> None:
        self.state_file.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    @staticmethod
    def checksum(path: Path) -> str:
        return sha256_file(Path(path))

    def verify(self, package: UpdatePackage) -> bool:
        """Return True only when package identity matches checksum_sha256.

        - File package: sha256(file) == checksum_sha256.
        - Directory package: embedded checksums manifest must verify (independent
          client) and checksum_sha256 must equal sha256 of that manifest file
          (identity of the manifest, not a self-hash inside it).
        """
        path = Path(package.path)
        if not path.exists():
            return False
        try:
            if path.is_dir():
                manifest = find_package_manifest(path)
                if manifest is None:
                    return False
                verify_checksum_manifest(manifest, root=path)
                return sha256_file(manifest) == package.checksum_sha256.lower()
            if not path.is_file():
                return False
            return sha256_file(path) == package.checksum_sha256.lower()
        except ChecksumManifestError:
            return False

    @staticmethod
    def write_package_manifest(package_dir: Path, files: list[Path] | None = None) -> Path:
        """Write ``checksums.txt`` inside package_dir for the given (or all) files."""
        package_dir = Path(package_dir)
        if not package_dir.is_dir():
            raise ChecksumManifestError(f"package dir missing: {package_dir}")
        out = package_dir / "checksums.txt"
        if files is None:
            files = [
                p
                for p in package_dir.iterdir()
                if p.is_file()
                and p.name
                not in {
                    "checksums.txt",
                    "checksums-linux.txt",
                    "SHA256SUMS.txt",
                    "SHA256SUMS",
                    "sha256sums.txt",
                }
            ]
        return write_checksum_manifest(files, out, root=package_dir)

    def backup_current(self) -> Path:
        version = self._state.get("version", "unknown")
        dest = self.backup_dir / f"backup-{version}"
        if dest.exists():
            shutil.rmtree(dest)
        if self.install_dir.exists():
            dest.mkdir(parents=True, exist_ok=True)
            for item in self.install_dir.iterdir():
                if item.name == "update_state.json":
                    continue
                target = dest / item.name
                if item.is_dir():
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)
        self._state.setdefault("backups", []).append(str(dest))
        self._save_state()
        return dest

    def _replace_install_tree(self, package: UpdatePackage) -> None:
        """Replace install tree from package (preserve update_state.json).

        Directory packages: install dir members become package members
        (sostituzione). File packages: copy the single artifact into install.
        """
        path = Path(package.path)
        if path.is_dir():
            package_names = {item.name for item in path.iterdir()}
            for item in list(self.install_dir.iterdir()):
                if item.name == "update_state.json":
                    continue
                if item.name not in package_names:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
            for item in path.iterdir():
                target = self.install_dir / item.name
                if item.is_dir():
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.copytree(item, target)
                else:
                    if target.exists() and target.is_dir():
                        shutil.rmtree(target)
                    shutil.copy2(item, target)
        else:
            shutil.copy2(path, self.install_dir / path.name)

    def apply(self, package: UpdatePackage, *, verify: bool = True) -> dict[str, Any]:
        """Apply package into install dir.

        N032: ``verify=False`` is forbidden on the distributed/public path.
        Use ``apply_windows_transactional`` for production Windows updates.
        """
        if not verify:
            return {
                "ok": False,
                "error": "verify=False forbidden on distributed apply path (N032)",
            }
        if not self.verify(package):
            return {"ok": False, "error": "checksum verification failed"}
        backup = self.backup_current()
        self._replace_install_tree(package)
        self._state["version"] = package.version
        self._state["last_backup"] = str(backup)
        self._save_state()
        return {"ok": True, "version": package.version, "backup": str(backup)}

    @staticmethod
    def _hook_succeeded(result: Mapping[str, Any] | None, *, kind: str) -> tuple[bool, str]:
        """Return (ok, error). Ambiguous health/status is not success."""
        if result is None:
            return True, ""
        if not isinstance(result, Mapping):
            return False, f"{kind}: non-mapping result"
        ok = result.get("ok")
        status = result.get("status")
        if kind == "health":
            # Explicit failure
            if ok is False:
                return False, str(result.get("error") or "health check failed")
            # Ambiguous: missing both conclusive ok=True and an OK status
            if ok is True:
                if status is None or str(status).lower() in _HEALTH_OK:
                    return True, ""
                return False, f"health status not ok: {status!r}"
            if status is not None and str(status).lower() in _HEALTH_OK:
                return True, ""
            return False, f"ambiguous health result (not success): {dict(result)!r}"
        # stop / start
        if ok is True:
            return True, ""
        if ok is False:
            return False, str(result.get("error") or f"{kind} failed")
        return False, f"ambiguous {kind} result (not success): {dict(result)!r}"

    def _install_dir_writable(self) -> tuple[bool, str]:
        """Return (ok, error). Fail closed if install_dir is not writable."""
        install = self.install_dir
        try:
            if not install.exists() or not install.is_dir():
                return False, f"install_dir missing or not a directory: {install}"
            if not os.access(install, os.W_OK):
                return False, f"permissions denied: install_dir not writable: {install}"
            probe = install / ".winos_update_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return True, ""
        except OSError as exc:
            return False, f"permissions denied on install_dir: {exc}"

    def _apply_os_transactional(
        self,
        package: UpdatePackage,
        *,
        stop_service: Hook | None = None,
        start_service: Hook | None = None,
        health_check: Hook | None = None,
        check_writable: bool = False,
        include_journal: bool = False,
        platform: str = "os",
    ) -> dict[str, Any]:
        """Shared transactional apply (N032 Windows / N033 Linux).

        Stages: preflight verify → [permissions] → backup → stop → replace →
        start → health. Always verifies. On failure after backup (or stop with
        need_rollback), rollback deletes files newly introduced by the update.
        Ambiguous health ≠ success. stop/start/health hooks are required (fail-closed if missing); hook exceptions trigger rollback. No live SCM/systemctl in CI.
        """
        stages: list[str] = ["preflight"]
        journal: list[str] = ["preflight: verify"]

        def _result(
            *,
            ok: bool,
            stage: str,
            error: str = "",
            backup: Path | None = None,
            rolled_back: bool = False,
            replaced: bool = False,
            version: str | None = None,
        ) -> dict[str, Any]:
            out: dict[str, Any] = {
                "ok": ok,
                "stage": stage,
                "stages": list(stages),
                "rolled_back": rolled_back,
                "platform": platform,
            }
            if error:
                out["error"] = error
            if backup is not None:
                out["backup"] = str(backup)
            if ok:
                out["version"] = version or package.version
                out["replaced"] = replaced
            if include_journal:
                out["journal"] = list(journal)
            return out

        if not self.verify(package):
            journal.append("preflight: checksum verification failed")
            return _result(
                ok=False,
                stage="preflight",
                error="checksum verification failed",
            )

        journal.append("preflight: ok")

        missing_hooks = [
            name
            for name, hook in (
                ("stop", stop_service),
                ("start", start_service),
                ("health", health_check),
            )
            if hook is None
        ]
        if missing_hooks:
            msg = "required lifecycle hooks missing: " + ", ".join(missing_hooks)
            journal.append(f"preflight: {msg}")
            return _result(ok=False, stage="preflight", error=msg)

        if check_writable:
            stages.append("permissions")
            journal.append("permissions: check install_dir writable")
            ok_w, err_w = self._install_dir_writable()
            if not ok_w:
                journal.append(f"permissions: fail — {err_w}")
                return _result(ok=False, stage="permissions", error=err_w)
            journal.append("permissions: ok")

        backup = self.backup_current()
        stages.append("backup")
        journal.append(f"backup: {backup}")
        rolled_past_replace = False

        def _fail(stage: str, error: str, *, need_rollback: bool) -> dict[str, Any]:
            rolled = False
            journal.append(f"{stage}: fail — {error}")
            if need_rollback:
                rb = self.rollback()
                rolled = bool(rb.get("ok"))
                if rolled:
                    journal.append("rollback: ok")
                else:
                    journal.append(f"rollback: fail — {rb.get('error')}")
                    error = f"{error}; rollback also failed: {rb.get('error')}"
            return _result(
                ok=False,
                stage=stage,
                error=error,
                backup=backup,
                rolled_back=rolled,
            )

        # stop (systemd/user or Windows SCM via injectable hook) — required
        stages.append("stop")
        journal.append("stop: begin")
        try:
            stop_result = stop_service()  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001
            return _fail("stop", f"stop hook raised: {exc}", need_rollback=True)
        ok, err = self._hook_succeeded(stop_result, kind="stop")
        if not ok:
            return _fail("stop", err, need_rollback=True)
        journal.append("stop: ok")

        # replace
        stages.append("replace")
        journal.append("replace: begin")
        try:
            self._replace_install_tree(package)
            rolled_past_replace = True
            self._state["version"] = package.version
            self._state["last_backup"] = str(backup)
            self._save_state()
            journal.append("replace: ok")
        except Exception as exc:  # noqa: BLE001 — boundary: any replace failure → rollback
            return _fail("replace", f"replace failed: {exc}", need_rollback=True)

        # start — required
        stages.append("start")
        journal.append("start: begin")
        try:
            start_result = start_service()  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001
            return _fail("start", f"start hook raised: {exc}", need_rollback=True)
        ok, err = self._hook_succeeded(start_result, kind="start")
        if not ok:
            return _fail("start", err, need_rollback=True)
        journal.append("start: ok")

        # health — required
        stages.append("health")
        journal.append("health: begin")
        try:
            health_result = health_check()  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001
            return _fail("health", f"health hook raised: {exc}", need_rollback=True)
        ok, err = self._hook_succeeded(health_result, kind="health")
        if not ok:
            return _fail("health", err, need_rollback=True)
        journal.append("health: ok")

        stages.append("done")
        journal.append("done")
        return _result(
            ok=True,
            stage="done",
            backup=backup,
            replaced=rolled_past_replace,
            version=package.version,
        )

    def apply_windows_transactional(
        self,
        package: UpdatePackage,
        *,
        stop_service: Hook | None = None,
        start_service: Hook | None = None,
        health_check: Hook | None = None,
    ) -> dict[str, Any]:
        """Transactional Windows update (N032 / H63-N032).

        Stages: preflight verify → backup → stop → replace → start → health.
        Always verifies (no verify=False). On failure after backup, rollback
        including deletion of files newly introduced by the update.
        Injectable hooks avoid live Windows SCM in CI.
        """
        return self._apply_os_transactional(
            package,
            stop_service=stop_service,
            start_service=start_service,
            health_check=health_check,
            check_writable=False,
            include_journal=False,
            platform="windows",
        )

    def apply_linux_transactional(
        self,
        package: UpdatePackage,
        *,
        stop_service: Hook | None = None,
        start_service: Hook | None = None,
        health_check: Hook | None = None,
    ) -> dict[str, Any]:
        """Transactional Linux update (N033 / H63-N033).

        Same state machine as N032 for systemd/user + Linux install tree:
        preflight verify → permissions → backup → stop → replace → start →
        health. Always verifies. Permission denied fail closed before replace
        (no orphans). Journal + stages trail. Injectable hooks named for
        systemd (stop_service/start_service/health_check) — no live systemctl
        required in CI. Ambiguous health ≠ success.
        """
        return self._apply_os_transactional(
            package,
            stop_service=stop_service,
            start_service=start_service,
            health_check=health_check,
            check_writable=True,
            include_journal=True,
            platform="linux",
        )

    def rollback(self) -> dict[str, Any]:
        """Restore latest backup and delete install files not present in it.

        N032: closes Phase 0 gap where rollback restored named files but left
        orphans introduced by a failed/partial update.
        """
        backups = self._state.get("backups") or []
        if not backups:
            return {"ok": False, "error": "no backup available"}
        latest = Path(backups[-1])
        if not latest.exists():
            return {"ok": False, "error": "backup missing"}

        backup_names = {item.name for item in latest.iterdir()} if latest.is_dir() else set()

        # Remove install members that are not in the backup (orphans from update).
        for item in list(self.install_dir.iterdir()):
            if item.name == "update_state.json":
                continue
            if item.name not in backup_names:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()

        for item in latest.iterdir():
            target = self.install_dir / item.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
        ver = latest.name.replace("backup-", "", 1)
        self._state["version"] = ver
        self._save_state()
        return {"ok": True, "version": ver, "restored_from": str(latest)}

    def discover(self, channel_url: str) -> ReleaseInfo:
        """HTTPS channel discovery (N031)."""
        return discover_release(channel_url)

    def discover_and_stage(
        self,
        channel_url: str,
        staging_dir: str | Path,
        *,
        allow_downgrade: bool = False,
        max_bytes: int = 256 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Discover + download + hash-verify into staging; enforce no-downgrade."""
        try:
            info = discover_release(channel_url)
            path = download_verified(
                info,
                staging_dir,
                max_bytes=max_bytes,
                current_version=self.version,
                allow_downgrade=allow_downgrade,
            )
        except ReleaseDiscoveryError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "version": info.version,
            "path": str(path),
            "sha256": info.sha256,
            "channel": info.channel,
        }

    @property
    def version(self) -> str:
        return str(self._state.get("version", "1.0.0"))
