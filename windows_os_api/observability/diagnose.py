"""N044 — Protected diagnose / support-bundle collection.

Builds a size-limited, redacted support bundle (stack traces, process snapshot,
config without secrets, correlated audit tail) intended for collection *before*
a service restart. Bundles are for authorized principals only (ADMIN on REST;
local CLI assumes operator host access and still redacts secrets).

Never embeds API keys, integrity secrets, or UI credential material.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from windows_os_api.core.security.audit import redact_audit_value, sanitize_audit_string

# Hard caps — blocked/hung runtimes must still produce a *limited* artifact.
DEFAULT_MAX_BUNDLE_BYTES = 262_144  # 256 KiB
MAX_MAX_BUNDLE_BYTES = 1_048_576  # 1 MiB absolute clamp
MIN_MAX_BUNDLE_BYTES = 4_096
MAX_STACK_THREADS = 32
MAX_STACK_FRAMES = 40
MAX_PROCESS_CHILDREN = 32
MAX_AUDIT_TAIL = 50
MAX_LOG_LINES = 100
BUNDLE_FILE_MODE = 0o600
DEFAULT_PID_FILE = "logs/winos-api.pid"


def default_pid_file() -> Path:
    """Canonical service pidfile path (sibling of audit log under logs/)."""
    return Path(DEFAULT_PID_FILE)


def write_service_pid_file(
    path: str | Path | None = None,
    *,
    pid: int | None = None,
) -> Path:
    """Persist the live service PID so an external CLI can diagnose a hung runtime."""
    dest = Path(path) if path is not None else default_pid_file()
    dest.parent.mkdir(parents=True, exist_ok=True)
    value = int(os.getpid() if pid is None else pid)
    tmp = dest.with_suffix(dest.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(f"{value}\n", encoding="utf-8")
    os.replace(str(tmp), str(dest))
    try:
        os.chmod(str(dest), BUNDLE_FILE_MODE)
    except OSError:
        pass
    return dest


def clear_service_pid_file(
    path: str | Path | None = None,
    *,
    expected_pid: int | None = None,
) -> bool:
    """Remove pidfile if present; optionally only when it still names ``expected_pid``."""
    dest = Path(path) if path is not None else default_pid_file()
    try:
        raw = dest.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return False
    if expected_pid is not None:
        try:
            if int(raw.split()[0]) != int(expected_pid):
                return False
        except (ValueError, IndexError):
            return False
    try:
        dest.unlink()
        return True
    except OSError:
        return False


def _read_pid_file(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    if not raw:
        return None
    try:
        pid = int(raw.split()[0])
    except ValueError:
        return None
    return pid if pid > 0 else None


def _pid_alive(pid: int) -> bool:
    """Reuse instance-lock liveness (PID-based, never clock-based)."""
    if pid <= 0:
        return False
    try:
        from windows_os_api.core.runtime.instance_lock import _pid_alive as lock_alive

        return bool(lock_alive(pid))
    except Exception:  # noqa: BLE001
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True
        return True


def resolve_target_pid(
    *,
    pid: int | None = None,
    pid_file: str | Path | None = None,
    prefer_service: bool = False,
) -> tuple[int, str]:
    """Resolve which process the bundle should describe.

    Returns ``(pid, source)`` where source is one of:
    ``explicit``, ``pid_file``, ``default_pid_file``, ``instance_lock``, ``self``.

    When ``prefer_service`` is True (CLI path), discover the live service via
    pidfile / instance lock before falling back to the collector process.
    """
    if pid is not None:
        target = int(pid)
        if target <= 0:
            raise DiagnoseParamError("pid must be a positive integer")
        return target, "explicit"

    if pid_file is not None:
        found = _read_pid_file(Path(pid_file))
        if found is None:
            raise DiagnoseParamError(f"pid file unreadable or empty: {pid_file}")
        return found, "pid_file"

    if prefer_service:
        found = _read_pid_file(default_pid_file())
        if found is not None and _pid_alive(found):
            return found, "default_pid_file"
        try:
            from windows_os_api.apps.adapters.store import store_dir
            from windows_os_api.core.runtime.instance_lock import read_holder

            holder = read_holder(store_dir())
            if holder and isinstance(holder.get("pid"), int):
                hpid = int(holder["pid"])
                if hpid > 0 and _pid_alive(hpid):
                    return hpid, "instance_lock"
        except Exception:  # noqa: BLE001 — discovery is best-effort
            pass

    return os.getpid(), "self"


# Settings / config key names that must never appear with values in a bundle.
_SECRET_SETTING_SUFFIXES = (
    "_key",
    "_keys",
    "_secret",
    "_token",
    "_password",
    "_credential",
    "_credentials",
)
_SECRET_SETTING_EXACT = frozenset(
    {
        "api_key",
        "api_keys",
        "password",
        "secret",
        "token",
        "authorization",
    }
)

REDACTED = "[REDACTED]"


class DiagnoseError(Exception):
    """Base diagnose / support-bundle failure."""


class DiagnoseParamError(DiagnoseError):
    """Invalid size / path / parameter."""


class DiagnoseWriteError(DiagnoseError):
    """Bundle path unwritable or ACL/mode failure."""


@dataclass(frozen=True)
class BundleWriteResult:
    path: Path
    bytes_written: int
    truncated: bool
    bundle_id: str
    before_restart: bool
    collected_at: str


def clamp_max_bytes(max_bytes: int | None) -> int:
    if max_bytes is None:
        return DEFAULT_MAX_BUNDLE_BYTES
    try:
        n = int(max_bytes)
    except (TypeError, ValueError) as exc:
        raise DiagnoseParamError("max_bytes must be an integer") from exc
    if n < MIN_MAX_BUNDLE_BYTES or n > MAX_MAX_BUNDLE_BYTES:
        raise DiagnoseParamError(
            f"max_bytes must be in [{MIN_MAX_BUNDLE_BYTES}, {MAX_MAX_BUNDLE_BYTES}]"
        )
    return n


def _is_secret_setting_key(name: str) -> bool:
    low = str(name).strip().lower()
    if low in _SECRET_SETTING_EXACT:
        return True
    # Drop plural/compound forms: admin_api_keys, audit_integrity_secret, …
    if any(low.endswith(suf) for suf in _SECRET_SETTING_SUFFIXES):
        return True
    if "api_key" in low or "password" in low or "secret" in low:
        return True
    return False


def redact_config(settings_map: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a config snapshot safe for support bundles (no key material)."""
    if not settings_map:
        return {}
    out: dict[str, Any] = {}
    for k, v in settings_map.items():
        key = str(k)
        if _is_secret_setting_key(key):
            # Keep presence/cardinality hints only — never values.
            if isinstance(v, (list, tuple)):
                out[key] = {"redacted": True, "count": len(v)}
            elif v:
                out[key] = {"redacted": True, "present": True}
            else:
                out[key] = {"redacted": True, "present": False}
            continue
        out[key] = redact_audit_value(v)
    return out


def capture_stack_snapshot(
    *,
    pid: int | None = None,
    max_threads: int = MAX_STACK_THREADS,
    max_frames: int = MAX_STACK_FRAMES,
) -> list[dict[str, Any]]:
    """Capture limited per-thread stacks (no locals — avoid secret leakage).

    When ``pid`` is this process (or omitted), use in-process frames. When
    targeting another (possibly hung) service PID, use best-effort external
    collection — Python frames are not available without a debugger.
    """
    target = os.getpid() if pid is None else int(pid)
    if target == os.getpid():
        frames = sys._current_frames()  # noqa: SLF001 — intentional diagnose aid
        names = {t.ident: t.name for t in threading.enumerate()}
        items: list[dict[str, Any]] = []
        for i, (tid, frame) in enumerate(frames.items()):
            if i >= max_threads:
                break
            stack = traceback.format_stack(frame, limit=max_frames)
            safe_stack = [sanitize_audit_string(line.rstrip(), max_len=512) for line in stack]
            items.append(
                {
                    "thread_id": int(tid) if tid is not None else None,
                    "thread_name": sanitize_audit_string(names.get(tid, "?"), max_len=128),
                    "stack": safe_stack,
                    "source": "in_process",
                }
            )
        return items
    return _capture_external_stacks(target, max_threads=max_threads, max_frames=max_frames)


def _capture_external_stacks(
    pid: int,
    *,
    max_threads: int,
    max_frames: int,
) -> list[dict[str, Any]]:
    """Best-effort stacks for another process (hung-service diagnose path)."""
    items: list[dict[str, Any]] = []
    # Linux: /proc/<pid>/task/<tid>/stack (kernel stacks; may be empty/EPERM).
    task_dir = Path(f"/proc/{pid}/task")
    if task_dir.is_dir():
        try:
            tids = sorted(int(p.name) for p in task_dir.iterdir() if p.name.isdigit())
        except OSError:
            tids = []
        for i, tid in enumerate(tids[:max_threads]):
            stack_lines: list[str] = []
            try:
                raw = (task_dir / str(tid) / "stack").read_text(encoding="utf-8", errors="replace")
                for line in raw.splitlines()[:max_frames]:
                    stack_lines.append(sanitize_audit_string(line, max_len=512))
            except OSError as exc:
                stack_lines = [
                    sanitize_audit_string(f"<unavailable:{type(exc).__name__}>", max_len=128)
                ]
            items.append(
                {
                    "thread_id": tid,
                    "thread_name": f"tid-{tid}",
                    "stack": stack_lines,
                    "source": "procfs",
                }
            )
        if items:
            return items
    # Portable fallback: thread ids/status via psutil (no Python frames).
    try:
        import psutil

        proc = psutil.Process(pid)
        for i, th in enumerate(proc.threads()[:max_threads]):
            items.append(
                {
                    "thread_id": int(getattr(th, "id", 0) or 0),
                    "thread_name": f"tid-{getattr(th, 'id', '?')}",
                    "stack": [
                        sanitize_audit_string(
                            "external_limited:no_python_frames",
                            max_len=128,
                        )
                    ],
                    "source": "psutil",
                    "user_time": float(getattr(th, "user_time", 0.0) or 0.0),
                    "system_time": float(getattr(th, "system_time", 0.0) or 0.0),
                }
            )
    except Exception as exc:  # noqa: BLE001
        items.append(
            {
                "thread_id": None,
                "thread_name": "external",
                "stack": [
                    sanitize_audit_string(
                        f"external_unavailable:{type(exc).__name__}",
                        max_len=128,
                    )
                ],
                "source": "unavailable",
            }
        )
    return items


def capture_process_snapshot(
    *,
    pid: int | None = None,
    max_children: int = MAX_PROCESS_CHILDREN,
) -> dict[str, Any]:
    """Snapshot of the target process (and limited children), not a full host dump."""
    pid = os.getpid() if pid is None else int(pid)
    collector = os.getpid()
    out: dict[str, Any] = {
        "pid": pid,
        "collector_pid": collector,
        "self": pid == collector,
    }
    if pid == collector:
        out["ppid"] = os.getppid()
        out["executable"] = sanitize_audit_string(sys.executable, max_len=512)
        out["argv0"] = sanitize_audit_string(sys.argv[0] if sys.argv else "", max_len=512)
        out["cwd"] = sanitize_audit_string(os.getcwd(), max_len=512)
        out["threads"] = threading.active_count()
    try:
        import psutil

        proc = psutil.Process(pid)
        with proc.oneshot():
            out["name"] = sanitize_audit_string(proc.name(), max_len=128)
            out["status"] = sanitize_audit_string(str(proc.status()), max_len=64)
            out["create_time"] = float(proc.create_time())
            if pid != collector:
                try:
                    out["ppid"] = int(proc.ppid())
                except (psutil.Error, OSError):
                    pass
                try:
                    out["executable"] = sanitize_audit_string(proc.exe(), max_len=512)
                except (psutil.Error, OSError):
                    pass
                try:
                    cmdline = proc.cmdline()
                    out["argv0"] = sanitize_audit_string(
                        cmdline[0] if cmdline else "", max_len=512
                    )
                    out["cmdline"] = [
                        sanitize_audit_string(c, max_len=256) for c in cmdline[:32]
                    ]
                except (psutil.Error, OSError):
                    pass
                try:
                    out["cwd"] = sanitize_audit_string(proc.cwd(), max_len=512)
                except (psutil.Error, OSError):
                    pass
                try:
                    out["threads"] = int(proc.num_threads())
                except (psutil.Error, OSError):
                    pass
            try:
                mem = proc.memory_info()
                out["memory_rss"] = int(mem.rss)
                out["memory_vms"] = int(mem.vms)
            except (psutil.Error, OSError):
                pass
            try:
                out["cpu_percent"] = float(proc.cpu_percent(interval=None))
            except (psutil.Error, OSError):
                pass
            try:
                out["num_fds"] = int(proc.num_fds()) if hasattr(proc, "num_fds") else None
            except (psutil.Error, OSError, AttributeError):
                out["num_fds"] = None
            children: list[dict[str, Any]] = []
            try:
                for ch in proc.children(recursive=False)[:max_children]:
                    try:
                        children.append(
                            {
                                "pid": ch.pid,
                                "name": sanitize_audit_string(ch.name(), max_len=128),
                                "status": sanitize_audit_string(str(ch.status()), max_len=64),
                            }
                        )
                    except (psutil.Error, OSError):
                        children.append({"pid": ch.pid, "error": "unavailable"})
            except (psutil.Error, OSError):
                pass
            out["children"] = children
    except Exception as exc:  # noqa: BLE001 — diagnose must not fail closed on psutil
        out["psutil_error"] = sanitize_audit_string(type(exc).__name__, max_len=64)
    return out


def capture_correlated_audit(*, limit: int = MAX_AUDIT_TAIL) -> dict[str, Any]:
    """Tail of integrity-verified, already-redacted audit entries + correlation ids."""
    limit = max(1, min(int(limit), MAX_AUDIT_TAIL))
    try:
        from windows_os_api.core.security.audit import (
            AuditIntegrityError,
            AuditUnavailableError,
            get_audit_logger,
        )

        logger = get_audit_logger()
        try:
            # Read a verified page then take the true tail.
            probe = logger.read_page(limit=1, offset=0)
            total = int(probe.get("total") or 0)
            offset = max(0, total - limit)
            page = logger.read_page(limit=limit, offset=offset)
            entries = []
            for e in page.get("entries") or []:
                row = {
                    k: v
                    for k, v in e.items()
                    if k not in ("hmac",)  # keep prev_hash for chain correlation
                }
                # Double-scrub detail defensively.
                if "detail" in row:
                    row["detail"] = redact_audit_value(row["detail"])
                entries.append(row)
            return {
                "available": True,
                "integrity_ok": True,
                "total": total,
                "returned": len(entries),
                "entries": entries,
                "path": str(logger.path),
            }
        except AuditIntegrityError as exc:
            return {
                "available": True,
                "integrity_ok": False,
                "error": "audit_integrity_failure",
                "reason": sanitize_audit_string(str(exc), max_len=256),
                "entries": [],
            }
        except AuditUnavailableError as exc:
            return {
                "available": False,
                "integrity_ok": False,
                "error": "audit_unavailable",
                "reason": sanitize_audit_string(str(exc), max_len=256),
                "entries": [],
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "integrity_ok": False,
            "error": "audit_probe_failed",
            "reason": sanitize_audit_string(type(exc).__name__, max_len=64),
            "entries": [],
        }


def capture_metrics_snapshot() -> dict[str, Any]:
    try:
        from windows_os_api.observability.metrics import get_metrics

        snap = get_metrics().snapshot(collect=True)
        return redact_audit_value(snap)
    except Exception as exc:  # noqa: BLE001
        return {"error": sanitize_audit_string(type(exc).__name__, max_len=64)}


def capture_health_snapshot() -> dict[str, Any]:
    """Best-effort live/ready probe — must not raise if backend is blocked."""
    out: dict[str, Any] = {}
    try:
        from windows_os_api.os.runtime_health import probe_liveness, probe_runtime_health

        out["live"] = probe_liveness()
        try:
            out["ready"] = probe_runtime_health()
        except Exception as exc:  # noqa: BLE001
            out["ready"] = {
                "ready": False,
                "error": sanitize_audit_string(type(exc).__name__, max_len=64),
            }
    except Exception as exc:  # noqa: BLE001
        out["error"] = sanitize_audit_string(type(exc).__name__, max_len=64)
    return out


def _settings_map() -> dict[str, Any]:
    try:
        from windows_os_api.core.runtime.config import get_settings

        s = get_settings()
        if hasattr(s, "model_dump"):
            return dict(s.model_dump())
        return dict(getattr(s, "__dict__", {}))
    except Exception:  # noqa: BLE001
        return {}


def build_support_bundle(
    *,
    reason: str = "manual",
    request_id: str | None = None,
    execution_id: str | None = None,
    before_restart: bool = False,
    max_bytes: int | None = None,
    include_audit: bool = True,
    target_pid: int | None = None,
    pid_file: str | Path | None = None,
    prefer_service: bool = False,
) -> dict[str, Any]:
    """Assemble a redacted support bundle, truncating to ``max_bytes`` if needed.

    When ``prefer_service`` / ``target_pid`` / ``pid_file`` select another process
    (CLI diagnosing a hung service), process + stacks describe that target, not
    the collector CLI PID.
    """
    max_b = clamp_max_bytes(max_bytes)
    bundle_id = str(uuid.uuid4())
    req = request_id or str(uuid.uuid4())
    exe = execution_id or str(uuid.uuid4())
    collected_at = datetime.now(timezone.utc).isoformat()
    resolved_pid, target_source = resolve_target_pid(
        pid=target_pid,
        pid_file=pid_file,
        prefer_service=prefer_service,
    )

    from windows_os_api import __version__

    body: dict[str, Any] = {
        "schema": "winos.support_bundle.v1",
        "bundle_id": bundle_id,
        "request_id": req,
        "execution_id": exe,
        "collected_at": collected_at,
        "reason": sanitize_audit_string(reason, max_len=128),
        "before_restart": bool(before_restart),
        "version": __version__,
        "platform": {
            "system": sys.platform,
            "python": sanitize_audit_string(sys.version.split()[0], max_len=32),
        },
        "collector": {
            "pid": os.getpid(),
            "target_pid": resolved_pid,
            "target_source": target_source,
        },
        "process": capture_process_snapshot(pid=resolved_pid),
        "stacks": capture_stack_snapshot(pid=resolved_pid),
        "config": redact_config(_settings_map()),
        "health": capture_health_snapshot(),
        "metrics": capture_metrics_snapshot(),
    }
    if include_audit:
        body["audit"] = capture_correlated_audit()

    truncated = False
    raw = json.dumps(body, ensure_ascii=False, default=str)
    if len(raw.encode("utf-8")) > max_b:
        truncated = True
        # Drop heaviest sections first while keeping identity + process.
        for key in ("stacks", "audit", "metrics", "config"):
            if key in body:
                body[key] = {"truncated": True, "reason": "max_bytes"}
            raw = json.dumps(body, ensure_ascii=False, default=str)
            if len(raw.encode("utf-8")) <= max_b:
                break
        # Last resort: shrink process children / health.
        if len(raw.encode("utf-8")) > max_b:
            body["process"] = {
                "pid": body.get("process", {}).get("pid"),
                "truncated": True,
            }
            body["health"] = {"truncated": True}
            raw = json.dumps(body, ensure_ascii=False, default=str)
        # Absolute hard cut (should be rare).
        encoded = raw.encode("utf-8")
        if len(encoded) > max_b:
            body = {
                "schema": "winos.support_bundle.v1",
                "bundle_id": bundle_id,
                "request_id": req,
                "execution_id": exe,
                "collected_at": collected_at,
                "before_restart": bool(before_restart),
                "truncated": True,
                "reason": sanitize_audit_string(reason, max_len=128),
                "error": "bundle_exceeded_max_bytes",
                "max_bytes": max_b,
            }
            truncated = True

    body["truncated"] = truncated
    body["max_bytes"] = max_b
    body["size_bytes"] = len(
        json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
    )
    # Recompute size after adding size_bytes (stable enough for limits).
    body["size_bytes"] = len(
        json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
    )
    return body


def write_support_bundle(
    path: str | Path,
    bundle: Mapping[str, Any] | None = None,
    *,
    max_bytes: int | None = None,
    before_restart: bool = False,
    reason: str = "manual",
    request_id: str | None = None,
    target_pid: int | None = None,
    pid_file: str | Path | None = None,
    prefer_service: bool = False,
) -> BundleWriteResult:
    """Atomically write a redacted bundle with owner-only file mode (0o600)."""
    dest = Path(path)
    if dest.exists() and dest.is_dir():
        raise DiagnoseParamError("output path must be a file, not a directory")
    if not dest.parent.exists():
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DiagnoseWriteError(f"cannot create parent: {exc}") from exc

    payload = dict(
        bundle
        or build_support_bundle(
            reason=reason,
            request_id=request_id,
            before_restart=before_restart,
            max_bytes=max_bytes,
            target_pid=target_pid,
            pid_file=pid_file,
            prefer_service=prefer_service,
        )
    )
    if before_restart:
        payload["before_restart"] = True

    data = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    encoded = data.encode("utf-8")
    max_b = clamp_max_bytes(max_bytes if max_bytes is not None else payload.get("max_bytes"))
    truncated = bool(payload.get("truncated"))
    if len(encoded) > max_b:
        # Rebuild with stricter cap.
        payload = build_support_bundle(
            reason=reason,
            request_id=request_id or payload.get("request_id"),
            before_restart=before_restart,
            max_bytes=max_b,
            target_pid=target_pid,
            pid_file=pid_file,
            prefer_service=prefer_service,
        )
        data = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
        encoded = data.encode("utf-8")
        truncated = True
        if len(encoded) > max_b:
            # Compact JSON without indent as last resort.
            data = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
            encoded = data.encode("utf-8")[:max_b]
            truncated = True

    tmp = dest.with_suffix(dest.suffix + f".tmp.{os.getpid()}")
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(str(tmp), flags, BUNDLE_FILE_MODE)
        try:
            os.write(fd, encoded)
            # fchmod is POSIX-only (missing on Windows).
            if hasattr(os, "fchmod"):
                try:
                    os.fchmod(fd, BUNDLE_FILE_MODE)
                except OSError:
                    pass
        finally:
            os.close(fd)
        os.replace(str(tmp), str(dest))
        try:
            os.chmod(str(dest), BUNDLE_FILE_MODE)
        except OSError:
            pass
    except OSError as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise DiagnoseWriteError(f"cannot write bundle: {exc}") from exc

    return BundleWriteResult(
        path=dest,
        bytes_written=dest.stat().st_size,
        truncated=truncated,
        bundle_id=str(payload.get("bundle_id") or ""),
        before_restart=bool(payload.get("before_restart")),
        collected_at=str(payload.get("collected_at") or ""),
    )


def collect_before_restart(
    path: str | Path,
    *,
    reason: str = "before_restart",
    max_bytes: int | None = None,
    request_id: str | None = None,
    subject: str = "cli",
    target_pid: int | None = None,
    pid_file: str | Path | None = None,
    prefer_service: bool = False,
) -> BundleWriteResult:
    """Collect a support bundle *before* any restart action.

    Does **not** restart the process — operators/supervisors restart after this
    returns successfully. Audits ``diagnose.collect_before_restart``.
    """
    result = write_support_bundle(
        path,
        before_restart=True,
        reason=reason,
        max_bytes=max_bytes,
        request_id=request_id,
        target_pid=target_pid,
        pid_file=pid_file,
        prefer_service=prefer_service,
    )
    try:
        from windows_os_api.core.security.audit import get_audit_logger
        from windows_os_api.observability.metrics import get_metrics

        get_audit_logger().log(
            "diagnose.collect_before_restart",
            subject=subject,
            resource=str(result.path),
            outcome="success",
            detail={
                "bundle_id": result.bundle_id,
                "bytes_written": result.bytes_written,
                "truncated": result.truncated,
                "before_restart": True,
            },
            request_id=request_id,
        )
        get_metrics().incr("diagnose.bundles")
        if result.truncated:
            get_metrics().incr("diagnose.truncated")
    except Exception:  # noqa: BLE001 — audit/metrics best-effort
        pass
    return result


def default_bundle_path(directory: str | Path | None = None) -> Path:
    """Default path under logs/ (or override dir) for crash/diagnose artifacts."""
    if directory is None:
        base = Path("logs")
    else:
        raw = str(directory)
        # Reject traversal / absolute escape attempts from untrusted query params.
        if ".." in Path(raw).parts or raw.strip().startswith("~"):
            raise DiagnoseParamError("output_dir must not contain '..' or home shortcuts")
        base = Path(raw)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return base / f"support-bundle-{ts}-{os.getpid()}.json"
