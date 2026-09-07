#!/usr/bin/env python3
"""Live smoke: LinuxBackend starts a real process via the same path as the API.

Uses LinuxBackend directly (and optionally TestClient) — no long-lived server required.
Asserts real PID via psutil.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("WINOS_BACKEND", "linux")
os.environ.setdefault("WINOS_REQUIRE_AUTH", "true")
os.environ.setdefault("WINOS_API_KEYS", '["dev-key-change-me"]')


def main() -> int:
    import psutil
    from windows_os_api.backends.linux import LinuxBackend
    from windows_os_api.core.runtime.config import get_settings
    from windows_os_api.backends.factory import reset_backend, get_backend

    sandbox = ROOT / "sandbox" / "live_smoke"
    sandbox.mkdir(parents=True, exist_ok=True)
    os.environ["WINOS_SANDBOX_ROOT"] = str(sandbox)
    get_settings.cache_clear()
    reset_backend()

    backend = LinuxBackend(sandbox_root=str(sandbox))
    info = backend.get_system_info()
    print("system_info:", info)
    assert info["backend"] == "linux"
    assert "Linux" in info["os"]

    # Prefer jq binary if present (Downloads or PATH), else /bin/echo
    jq_dl = Path("/home/box/Downloads/jq-linux-amd64")
    if jq_dl.is_file() and os.access(jq_dl, os.X_OK):
        cmd, args = str(jq_dl), ["--version"]
    elif Path("/usr/bin/jq").is_file():
        cmd, args = "/usr/bin/jq", ["--version"]
    else:
        cmd, args = "/bin/echo", ["live-linux-smoke"]

    print(f"start_process: {cmd} {args}")
    started = backend.start_process(cmd, args)
    print("started:", started)
    assert started.get("ok", True) is not False
    pid = started["pid"]
    assert pid > 1
    # Real process existed (may exit quickly for --version / echo)
    existed = psutil.pid_exists(pid) or started.get("real") is True
    assert existed, f"PID {pid} never looked real"
    # For long-lived check also start sleep
    sleep_started = backend.start_process("/bin/sleep", ["5"])
    spid = sleep_started["pid"]
    assert psutil.Process(spid).is_running()
    print(f"real sleep pid={spid} running={psutil.Process(spid).is_running()}")
    term = backend.terminate_process(spid)
    assert term["ok"]
    time.sleep(0.2)
    print("terminate:", term)

    # Factory path
    get_settings.cache_clear()
    reset_backend()
    os.environ["WINOS_BACKEND"] = "linux"
    b = get_backend()
    assert b.name == "linux"
    print("factory backend:", b.name)

    # Optional API TestClient path
    from fastapi.testclient import TestClient
    from windows_os_api.core.runtime.app import create_app

    get_settings.cache_clear()
    reset_backend()
    app = create_app(get_settings())
    headers = {"X-API-Key": "dev-key-change-me"}
    with TestClient(app) as client:
        r = client.get("/v1/system", headers=headers)
        assert r.status_code == 200
        body = r.json()
        print("GET /v1/system:", body)
        assert body["backend"] == "linux"
        caps = client.get("/v1/capabilities", headers=headers).json()
        print("capabilities backend:", caps.get("backend"), "flags:", caps.get("feature_flags"))
        assert caps["backend"] == "linux"
        assert caps.get("feature_flags", {}).get("windows_uia") is False
        pr = client.post(
            "/v1/processes",
            headers=headers,
            json={"command": "/bin/sleep", "args": ["3"]},
        )
        print("POST /v1/processes:", pr.status_code, pr.text[:300])
        assert pr.status_code in (200, 201)
        pdata = pr.json()
        apid = pdata["pid"]
        assert psutil.Process(apid).is_running()
        tr = client.delete(f"/v1/processes/{apid}", headers=headers)
        print("DELETE process:", tr.status_code, tr.text[:200])
        assert tr.status_code == 200

    print("LIVE LINUX SMOKE OK — real PIDs via LinuxBackend + API")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
