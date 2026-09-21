"""CLI entrypoint: winos-api serve|version|audit|diagnose."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_api_key_file(path: str | Path) -> str:
    """Load exactly one non-empty API key without exposing it on the command line.

    Rejects empty and denylisted weak/dev keys via assert_release_api_key (N035).
    """
    from windows_os_api.installer.identity import assert_release_api_key

    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if len(lines) != 1 or not lines[0].strip():
        raise ValueError("API key file must contain exactly one non-empty line")
    return assert_release_api_key(lines[0])


def _run_forensic_audit() -> int:
    import runpy

    script = Path(__file__).resolve().parents[2] / "scripts" / "forensic_audit.py"
    # parents: cli -> windows_os_api -> project root
    runpy.run_path(str(script), run_name="__main__")
    return 0


def _audit_verify() -> int:
    from windows_os_api.core.security.audit import get_audit_logger, reset_audit_logger

    reset_audit_logger()
    logger = get_audit_logger()
    report = logger.verify_integrity()
    out = {
        "ok": report.ok,
        "available": report.available,
        "entries_checked": report.entries_checked,
        "reason": report.reason,
        "path": str(logger.path),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0 if report.ok and report.available else 2


def _audit_tail(limit: int) -> int:
    from windows_os_api.core.security.audit import (
        AuditIntegrityError,
        AuditUnavailableError,
        get_audit_logger,
        reset_audit_logger,
    )

    reset_audit_logger()
    logger = get_audit_logger()
    try:
        page = logger.read_page(limit=max(1, min(limit, 500)), offset=0)
    except (AuditIntegrityError, AuditUnavailableError) as exc:
        print(
            json.dumps(
                {"error": type(exc).__name__, "reason": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    # Tail = last N of the verified set
    entries = page["entries"]
    total = page["total"]
    start = max(0, total - max(1, min(limit, 500)))
    # Re-read with offset for true tail without secrets
    try:
        page = logger.read_page(limit=max(1, min(limit, 500)), offset=start)
    except (AuditIntegrityError, AuditUnavailableError) as exc:
        print(
            json.dumps(
                {"error": type(exc).__name__, "reason": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    # Strip hmac secret material is already not present; drop long hmac for brevity
    safe = []
    for e in page["entries"]:
        row = {k: v for k, v in e.items() if k not in ("hmac", "prev_hash")}
        safe.append(row)
    print(json.dumps({"entries": safe, "count": len(safe)}, ensure_ascii=False, indent=2))
    return 0


def _diagnose_collect(
    output: str | None,
    *,
    before_restart: bool,
    max_bytes: int | None,
    target_pid: int | None = None,
    pid_file: str | None = None,
) -> int:
    """N044 — write a redacted support bundle (optionally marked before-restart).

    Prefers the live service PID (pidfile / instance lock / --pid) so a hung
    runtime is described — not the short-lived CLI collector process.
    """
    from windows_os_api.observability.diagnose import (
        DiagnoseError,
        collect_before_restart,
        default_bundle_path,
        write_support_bundle,
    )

    path = output or str(default_bundle_path())
    try:
        if before_restart:
            result = collect_before_restart(
                path,
                reason="cli.before_restart",
                max_bytes=max_bytes,
                subject="cli",
                target_pid=target_pid,
                pid_file=pid_file,
                prefer_service=True,
            )
        else:
            result = write_support_bundle(
                path,
                reason="cli.diagnose",
                max_bytes=max_bytes,
                before_restart=False,
                target_pid=target_pid,
                pid_file=pid_file,
                prefer_service=True,
            )
    except DiagnoseError as exc:
        print(
            json.dumps(
                {"error": type(exc).__name__, "reason": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(result.path),
                "bytes_written": result.bytes_written,
                "truncated": result.truncated,
                "bundle_id": result.bundle_id,
                "before_restart": result.before_restart,
                "collected_at": result.collected_at,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="winos-api", description="Windows OS API Layer")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Start FastAPI server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--backend", choices=["auto", "fake", "windows"], default=None)
    serve.add_argument("--no-auth", action="store_true")
    serve.add_argument(
        "--api-key-file",
        default=None,
        help="read the sole API key from a one-line UTF-8 file",
    )

    sub.add_parser("version", help="Print version")

    audit_p = sub.add_parser(
        "audit",
        help="Forensic roadmap audit (default) or JSONL verify/tail (N042)",
    )
    audit_p.add_argument(
        "audit_cmd",
        nargs="?",
        default="forensic",
        choices=["forensic", "verify", "tail"],
        help="forensic=scripts/forensic_audit.py; verify/tail=app JSONL (N042)",
    )
    audit_p.add_argument(
        "--limit",
        type=int,
        default=20,
        help="tail entry count (audit tail)",
    )


    diag = sub.add_parser(
        "diagnose",
        help="Collect redacted support bundle (stack/process/config/audit) — N044",
    )
    diag.add_argument(
        "--output",
        "-o",
        default=None,
        help="output JSON path (default: logs/support-bundle-<ts>-<pid>.json)",
    )
    diag.add_argument(
        "--before-restart",
        action="store_true",
        help="mark bundle as collect-before-restart and audit that intent (does not restart)",
    )
    diag.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        help="hard size cap for the bundle (default 262144, max 1048576)",
    )
    diag.add_argument(
        "--pid",
        type=int,
        default=None,
        help="target service PID (default: logs/winos-api.pid or instance lock)",
    )
    diag.add_argument(
        "--pid-file",
        default=None,
        help="path to service pidfile (default: logs/winos-api.pid)",
    )

    args = parser.parse_args(argv)

    if args.cmd == "version":
        from windows_os_api import __version__

        print(__version__)
        return 0

    if args.cmd == "audit":
        cmd = getattr(args, "audit_cmd", "forensic") or "forensic"
        if cmd == "verify":
            return _audit_verify()
        if cmd == "tail":
            return _audit_tail(args.limit)
        return _run_forensic_audit()


    if args.cmd == "diagnose":
        return _diagnose_collect(
            args.output,
            before_restart=bool(args.before_restart),
            max_bytes=args.max_bytes,
            target_pid=args.pid,
            pid_file=args.pid_file,
        )

    if args.cmd == "serve":
        import os

        import uvicorn

        if args.backend:
            os.environ["WINOS_BACKEND"] = args.backend
        if args.no_auth:
            os.environ["WINOS_REQUIRE_AUTH"] = "false"
        if args.api_key_file:
            try:
                api_key = load_api_key_file(args.api_key_file)
            except (OSError, UnicodeError, ValueError) as exc:
                print(f"Unable to load API key file: {exc}", file=sys.stderr)
                return 2
            os.environ["WINOS_API_KEYS"] = json.dumps([api_key])
            os.environ["WINOS_REQUIRE_AUTH"] = "true"

        from windows_os_api.core.runtime.config import get_settings

        get_settings.cache_clear()
        settings = get_settings()
        host = args.host or settings.effective_host()
        port = args.port or settings.port
        if not settings.remote_access_enabled and host not in ("127.0.0.1", "localhost", "::1"):
            print("Remote access disabled — forcing 127.0.0.1", file=sys.stderr)
            host = "127.0.0.1"
        uvicorn.run(
            "windows_os_api.core.runtime.app:create_app",
            factory=True,
            host=host,
            port=port,
            reload=False,
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
