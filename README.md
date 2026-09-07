# WinOs-Layer — Windows OS API Layer + Universal Adapter

Piattaforma **FastAPI** (non un simulatore React) che espone Windows come OS API e trasforma applicazioni EXE in **Virtual API** tramite Universal Adapter.

Layers: `OS Layer → Automation Engine → Universal Adapter → Virtual API → AI/MCP`

## Features

- FastAPI + OpenAPI (`/docs`), bind default **127.0.0.1**, remote access **DISABLED**
- Security: API keys, RBAC, permissions, audit log, rate limiting
- `WindowsBackend` (guarded imports) + `FakeBackend` (Linux E2E hard-real)
- Universal Adapter su albero UI (fixture CRM Contoso)
- MCP JSON-RPC server, WebSocket event bus, Control Center HTML
- Roadmap PR #1–#50 con `docs/FORENSIC_AUDIT.md`

## Layout

```
windows_os_api/     # Python package
  core/             # runtime, security, permissions, events
  os/               # system, processes, filesystem, windows, ...
  apps/             # discovery, adapters, workflows, agent, ...
  api/              # rest, websocket, mcp
  backends/         # fake.py, windows.py
  control_center/   # HTML dashboard
tests/
docs/FORENSIC_AUDIT.md
scripts/forensic_audit.py
.github/workflows/
```

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run server

```bash
export WINOS_BACKEND=fake          # on Linux; auto/windows on Win32
export WINOS_API_KEYS='["dev-key-change-me"]'
winos-api serve --host 127.0.0.1 --port 8765
# or: python -m windows_os_api.cli.main serve
```

Open:
- API docs: http://127.0.0.1:8765/docs
- Control Center: http://127.0.0.1:8765/
- Header: `X-API-Key: dev-key-change-me`

## Tests

```bash
export WINOS_BACKEND=fake
pytest -q
```

## Forensic audit

```bash
python scripts/forensic_audit.py
```

Fails if any `DONE` claim in `docs/forensic_audit.json` lacks files/tests.

## MCP

```bash
winos-mcp   # stdio JSON-RPC
# or: python -m windows_os_api.api.mcp.server
```

## Push to GitHub

Repo target: https://github.com/zarbopiero963-droid/WinOs-Layer-

```bash
cd /workspace/WinOs-Layer-   # or your clone path
git remote add origin https://github.com/zarbopiero963-droid/WinOs-Layer-.git
git push -u origin main
```

If the remote already has commits, use `git pull --rebase origin main` first, or force only if you intend to overwrite.

## Permissions

`system.read`, `filesystem.read/write`, `process.read/execute`, `ui.read/control`, `network.read`, `service.control`, `admin`, …

## Note

On Linux CI, `FakeBackend` provides hard E2E. `WindowsBackend` raises if instantiated off Win32; COM/UIA are stubbed until `pywin32`/`comtypes` are present on Windows.
