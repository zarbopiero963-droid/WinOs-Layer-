"""Auto-update with verify / backup / rollback.

N028: directory packages verify via an embedded checksum manifest that never
lists itself; duplicate basenames, missing or altered files fail closed.
"""
from __future__ import annotations

import json
import shutil
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

    def apply(self, package: UpdatePackage, *, verify: bool = True) -> dict[str, Any]:
        if verify and not self.verify(package):
            return {"ok": False, "error": "checksum verification failed"}
        backup = self.backup_current()
        if package.path.is_dir():
            for item in package.path.iterdir():
                target = self.install_dir / item.name
                if item.is_dir():
                    if target.exists():
                        shutil.rmtree(target)
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)
        else:
            shutil.copy2(package.path, self.install_dir / package.path.name)
        self._state["version"] = package.version
        self._state["last_backup"] = str(backup)
        self._save_state()
        return {"ok": True, "version": package.version, "backup": str(backup)}

    def rollback(self) -> dict[str, Any]:
        backups = self._state.get("backups") or []
        if not backups:
            return {"ok": False, "error": "no backup available"}
        latest = Path(backups[-1])
        if not latest.exists():
            return {"ok": False, "error": "backup missing"}
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
