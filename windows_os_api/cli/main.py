"""CLI entrypoint: winos-api serve|version|audit."""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="winos-api", description="Windows OS API Layer")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Start FastAPI server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--backend", choices=["auto", "fake", "windows"], default=None)
    serve.add_argument("--no-auth", action="store_true")

    sub.add_parser("version", help="Print version")
    sub.add_parser("audit", help="Run forensic audit script")

    args = parser.parse_args(argv)

    if args.cmd == "version":
        from windows_os_api import __version__

        print(__version__)
        return 0

    if args.cmd == "audit":
        from pathlib import Path
        import runpy

        script = Path(__file__).resolve().parents[2] / "scripts" / "forensic_audit.py"
        # parents: cli -> windows_os_api -> project root? 
        # Path: windows_os_api/cli/main.py -> parents[0]=cli, [1]=windows_os_api, [2]=project
        runpy.run_path(str(script), run_name="__main__")
        return 0

    if args.cmd == "serve":
        import os
        import uvicorn

        if args.backend:
            os.environ["WINOS_BACKEND"] = args.backend
        if args.no_auth:
            os.environ["WINOS_REQUIRE_AUTH"] = "false"

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
