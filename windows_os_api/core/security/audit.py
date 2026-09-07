"""Append-only JSONL audit logger."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(
        self,
        action: str,
        *,
        subject: str = "system",
        resource: str = "",
        outcome: str = "success",
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "subject": subject,
            "resource": resource,
            "outcome": outcome,
            "detail": detail or {},
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        return entry

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries


_audit: AuditLogger | None = None


def get_audit_logger(path: str | None = None) -> AuditLogger:
    global _audit
    if _audit is None:
        from windows_os_api.core.runtime.config import get_settings

        p = path or get_settings().audit_log_path
        _audit = AuditLogger(p)
    return _audit


def reset_audit_logger() -> None:
    global _audit
    _audit = None
