"""CLI entrypoint: winos-api serve|version|audit."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_api_key_file(path: str | Path) -> str:
    """Load exactly one non-empty API key without exposing it on the command line."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if len(lines) != 1 or not lines[0].strip():
        raise ValueError("API key file must contain exactly one non-empty line")
    return lines[0].strip()


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
    sub.add_parser("audit", help="Run forensic audit script")

    args = parser.parse_args(argv)

    if args.cmd == "version":
        from windows_os_api import __version__

        print(__version__)
        return 0

    if args.cmd == "audit":
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
