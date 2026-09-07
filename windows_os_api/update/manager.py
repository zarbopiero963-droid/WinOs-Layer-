"""Auto-update with verify / backup / rollback."""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def verify(self, package: UpdatePackage) -> bool:
        if not package.path.exists():
            return False
        return self.checksum(package.path) == package.checksum_sha256

    def backup_current(self) -> Path:
        version = self._state.get("version", "unknown")
        dest = self.backup_dir / f"backup-{version}"
        if dest.exists():
            shutil.rmtree(dest)
        if self.install_dir.exists():
            # Copy files except update_state and backups nesting
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
        # Extract: for tests, package.path is a directory or a single file payload
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
            # Single file update payload — copy as release.txt content marker
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
        # Restore version from backup folder name
        ver = latest.name.replace("backup-", "", 1)
        self._state["version"] = ver
        self._save_state()
        return {"ok": True, "version": ver, "restored_from": str(latest)}

    @property
    def version(self) -> str:
        return str(self._state.get("version", "1.0.0"))
