# WinOs-Layer — Windows OS API Layer + Universal Adapter

Piattaforma **FastAPI** (non un simulatore React) che espone il sistema operativo come OS API e trasforma applicazioni in **Virtual API** tramite Universal Adapter.

Layers: `OS Layer → Automation Engine → Universal Adapter → Virtual API → AI/MCP`

## Dual platform backends

| Backend | When | Behavior |
|---------|------|----------|
| **WindowsBackend** | Win32 (`WINOS_BACKEND=auto` or `windows`) | Real Windows processes, FS, registry, Win32 UI |
| **LinuxBackend** | Linux (`WINOS_BACKEND=auto` or `linux`) | **Real** Linux via psutil/subprocess/pathlib — not a simulator |
| **FakeBackend** | Explicit `WINOS_BACKEND=fake` only | Deterministic Contoso CRM fixtures for adapter E2E / CI |

`WINOS_BACKEND=auto` → **windows on win32**, **linux on Linux**. Fake is never the default on Linux.
Requesting `windows` on Linux raises unless `WINOS_ALLOW_FAKE_FALLBACK=true`.

## Features

- FastAPI + OpenAPI (`/docs`), bind default **127.0.0.1**, remote access **DISABLED**
- Security: API keys, RBAC, permissions, audit log, rate limiting
- Real OS ops on each platform; FakeBackend for CRM adapter demos
- Universal Adapter (UI tree on Fake / Windows UIA / optional Linux AT-SPI)
- MCP JSON-RPC server, WebSocket event bus, Control Center HTML
- Roadmap PR #1–#50 with `docs/FORENSIC_AUDIT.md`

## Layout

```
windows_os_api/     # Python package
  core/             # runtime, security, permissions, events
  os/               # system, processes, filesystem, windows, ...
  apps/             # discovery, adapters, workflows, agent, ...
  api/              # rest, websocket, mcp
  backends/         # linux.py, windows.py, fake.py, factory.py
  control_center/   # HTML dashboard
tests/              # unit, integration, e2e, linux/, windows/
docs/FORENSIC_AUDIT.md
scripts/forensic_audit.py
scripts/live_linux_smoke.py
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
# Linux: real LinuxBackend by default (auto)
export WINOS_BACKEND=auto          # or linux
# For Contoso CRM adapter demos only:
# export WINOS_BACKEND=fake
export WINOS_API_KEYS='["dev-key-change-me"]'
winos-api serve --host 127.0.0.1 --port 8765
# or: python -m windows_os_api.cli.main serve
```

Open:
- API docs: http://127.0.0.1:8765/docs
- Control Center: http://127.0.0.1:8765/
- Header: `X-API-Key: dev-key-change-me`

`POST /v1/processes` on LinuxBackend **actually starts** binaries (real PIDs via psutil).

## Tests

```bash
# Fake / portable suite (CRM fixtures)
export WINOS_BACKEND=fake
pytest -q -m "not windows and not linux"

# Real LinuxBackend hard tests
export WINOS_BACKEND=linux
pytest -q -m linux

# Everything except Windows-only
pytest -q -m "not windows"

# Live smoke (real sleep/jq PID via API)
python scripts/live_linux_smoke.py
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
| **Linux portable** | **Build Linux** → `dist-linux-portable` | `winos-api-portable-linux.zip` | FastAPI server ELF with **LinuxBackend** (real OS). Fake optional. **Not a Windows emulator.** |
| **Windows EXE/Setup** | **Build** → `dist-windows` | Setup.exe + `winos-api-portable-windows.zip` | **WindowsBackend** on Win32 |
| **Release** | tag `v*` | both attached | exe, windows zip, linux zip, checksums |

## Installer / packaging

```bash
python scripts/build_installer.py validate
python scripts/build_installer.py build-portable
python scripts/build_installer.py package-linux
python scripts/build_installer.py checksums
```

Service name: `WindowsOSLayerService`. Default bind: `127.0.0.1:8765`.
Linux systemd: `installer/linux/winos-api.service` (LinuxBackend / auto).

## Capabilities honesty

`GET /v1/capabilities` reports `backend` plus `feature_flags` (`windows_ui`, `atspi`, `windows_uia=false` on Linux, etc.).
Do **not** claim Windows UIA on Linux. Process / FS / system / network on LinuxBackend are **real**.

## Push to GitHub

Repo target: https://github.com/zarbopiero963-droid/WinOs-Layer-

```bash
cd /workspace/WinOs-Layer-
git remote add origin https://github.com/zarbopiero963-droid/WinOs-Layer-.git
git push -u origin main
```

## Permissions

`system.read`, `filesystem.read/write`, `process.read/execute`, `ui.read/control`, `network.read`, `service.control`, `admin`, …

## Note

- **LinuxBackend**: real processes, sandbox FS, psutil network/users; optional wmctrl/xdotool, xclip, pactl, systemctl, pyatspi — graceful degrade.
- **FakeBackend**: Contoso CRM UI tree for adapter unit/E2E — `WINOS_BACKEND=fake` only.
- **WindowsBackend**: raises if instantiated off Win32; COM/UIA when `pywin32`/`comtypes` present.
