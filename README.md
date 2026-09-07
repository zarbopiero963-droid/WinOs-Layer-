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

## Two builds — choose your OS

| Platform | GitHub Actions | Artifact | Notes |
|----------|----------------|----------|-------|
| **Linux portable** | **Build Linux** → `dist-linux-portable` | `winos-api-portable-linux.zip` | FastAPI server ELF with **FakeBackend** (OS-portable). **Not a Windows emulator.** |
| **Windows EXE/Setup** | **Build** → `dist-windows` | Setup.exe + `winos-api-portable-windows.zip` | **WindowsBackend** on Win32 |
| **Release** | tag `v*` | both attached | exe, windows zip, linux zip, checksums |

```bash
# Actions → Build Linux  → download artifact dist-linux-portable
# Actions → Build        → dist-windows (Setup.exe + portable zip)
# Release                → attaches both OS variants
```

## Installer / packaging

```bash
python scripts/build_installer.py validate
python scripts/build_installer.py build-portable   # current OS smoke (Linux ELF or Windows onefile)
python scripts/build_installer.py package-linux    # → dist/winos-api-portable-linux.zip
# Setup.exe: Windows only — see installer/README.md and GHA build.yml
python scripts/build_installer.py checksums
```

Service name: `WindowsOSLayerService`. Default bind: `127.0.0.1:8765`.
Linux systemd: `installer/linux/winos-api.service` (FakeBackend / localhost).

## Windows hard tests

```bash
# Linux CI
pytest -q -m "not windows"
# Windows GHA
pytest -q -m windows
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
