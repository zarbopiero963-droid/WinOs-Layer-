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
- Universal Adapter (UI tree on Fake / **real Windows UIA** / Linux AT-SPI with find/click/set-text)
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
scripts/live_windows_smoke.py
.github/workflows/
```

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -e ".[dev]"
# On Windows also: pip install -e ".[dev,windows]"
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


## Linux optional packages

```bash
# Screenshots + vision template OCR (Pillow/mss already in deps)
# Accurate OCR (optional):
sudo apt install tesseract-ocr
pip install pytesseract

# Audio (PulseAudio or PipeWire):
sudo apt install pulseaudio-utils     # provides pactl
# or: sudo apt install pipewire-utils  # provides wpctl

# Wayland input / window tools (compositor-dependent):
#   ydotool or wtype or dotool   — typing / clicks
#   wlrctl / swaymsg / hyprctl   — window list (wlroots/Sway/Hyprland)

# AT-SPI UI tree:
sudo apt install python3-pyatspi at-spi2-core

# X11 (still supported; auto-selected when XDG_SESSION_TYPE=x11):
sudo apt install wmctrl xdotool xclip
```

### Capability honesty (Linux)

`GET /v1/capabilities` `feature_flags` include: `ocr`, `ocr_tesseract`, `wayland`, `x11`,
`audio`, `services`, `vision`, `privileged` (always false — elevation is gated).

| Flag | Meaning |
|------|---------|
| `ocr` / `vision` | Pillow template matcher always; tesseract when installed |
| `wayland` | Session is Wayland; window/input tools may still be missing |
| `privileged` | Never open — needs `ADMIN` + `WINOS_ALLOW_PRIVILEGED=true` |
| `windows_uia` | Always false on Linux |

Vision OCR accuracy is best-effort without tesseract. Wayland window control varies by compositor.


## Optional AI providers (Windows + Linux)

Same config surface on both platforms. Default is **local** (Pillow / tesseract OCR) — no API key required.

| Env / setting | Values | Notes |
|---------------|--------|-------|
| `WINOS_AI_PROVIDER` | `local` \| `openai` \| `anthropic` \| `openrouter` | default `local` |
| `WINOS_AI_API_KEY` | secret | never logged in full; masked as `sk-…xxxx` in API |
| `WINOS_AI_MODEL` | optional | defaults per provider |
| `WINOS_AI_BASE_URL` | optional | OpenRouter / custom OpenAI-compatible |

Persist via Control Center **AI Provider** section or:

```bash
# Admin API key required
curl -s -X PUT http://127.0.0.1:8765/v1/ai/settings \
  -H "X-API-Key: admin-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"provider":"openai","api_key":"sk-YOUR_KEY_HERE","model":"gpt-4o-mini"}'

curl -s http://127.0.0.1:8765/v1/ai/settings -H "X-API-Key: admin-key-change-me"
curl -s -X POST http://127.0.0.1:8765/v1/ai/test \
  -H "X-API-Key: admin-key-change-me" -H "Content-Type: application/json" \
  -d '{"spend":false}'
```

Settings file: `~/.config/winos-api/ai_settings.json` (Linux) or `%APPDATA%\\winos-api\\ai_settings.json` (Windows), mode `0600`. Empty `api_key` on PUT clears the secret. Never commit real keys — use placeholders like `sk-YOUR_KEY_HERE`.

When `provider != local` and a key is set, vision/find and UI reasoner/planner may call the remote chat API; otherwise the existing local path is unchanged.

## Tests

```bash
# Fake / portable suite (CRM fixtures)
export WINOS_BACKEND=fake
pytest -q -m "not windows and not linux"

# Real LinuxBackend hard tests — need a reachable X display
export WINOS_BACKEND=linux
pytest -q -m linux

# Everything except Windows-only (must stay green on Linux CI)
pytest -q -m "not windows"

# Headless machine (CI, container, ssh without X)? Wrap with Xvfb, otherwise
# screenshot capture fails with "Cannot connect to display":
xvfb-run -a --server-args="-screen 0 1280x1024x24" pytest -q -m linux

# Live Linux smoke (real sleep/jq PID via API)
python scripts/live_linux_smoke.py
```

### Windows hard tests (win32 only)

```bash
# On a real Windows machine / GHA windows-latest:
pip install -e ".[dev,windows]"   # pywin32, comtypes, uiautomation, mss, Pillow
# Optional UIA fallback: pip install pywinauto
export WINOS_BACKEND=windows      # PowerShell: $env:WINOS_BACKEND="windows"

# Headless-capable hard tests (process/FS/registry/SendInput structures)
pytest -q -m windows

# UI Automation / screenshot tests need an interactive desktop
pytest -q -m "windows and requires_display"

# Live smoke (Notepad + tree + type + screenshot; skips UI if session 0)
python scripts/live_windows_smoke.py
```

On Linux, `pytest -m windows` collects import/smoke tests; runtime Win32 tests skip with clear reasons.
Without a real Windows **interactive desktop**, UIA tree / mouse click / screenshot may skip (`requires_display`); process, FS, registry, clipboard, and SendInput API calls still run.

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

### Artifact smoke — always run the binary you built

A PyInstaller onefile build failing is a **runtime** event, not a build one: a
dropped hidden import produces a binary that builds green and dies on launch.
So the frozen artifact is never shipped without being executed:

```bash
python scripts/build_installer.py build-portable
python scripts/artifact_smoke.py --expect-backend linux    # or: windows
```

It starts `dist/winos-api(.exe)`, polls `/v1/health` over real HTTP, asserts the
served backend and version match this source tree, checks that `/v1/system` is
refused without an API key, then verifies the process exits and frees its port.
Exit 0 means the artifact is usable; any other exit means do not ship it.

On Windows the same guard runs a second time against the **installed** copy:

```bash
python scripts/installer_smoke.py     # Windows only
```

Silent install of `WinOsApi-Setup-<version>.exe` into a scratch directory, check
of the layout the `.iss` promises (`winos-api.exe`, `service/`, a non-empty
`api_key.txt`), `artifact_smoke` against the installed binary, then silent
uninstall with a check that nothing is left behind. It does **not** assert a
registered Windows service: the installer does not register one — that is a
separate manual step (`service/install_nssm.bat`).

Wired into both `Build` and `Build Linux` right after `build-portable`. The
`Build` workflow also runs on pull requests that touch packaging (`build.yml`,
`installer/`, `build_installer.py`, `artifact_smoke.py`, `windows_os_api/cli/`,
`pyproject.toml`), so a packaging change proves the artifact still runs before
it merges — without making every unrelated PR pay for a Windows runner.

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

- **LinuxBackend**: real processes, sandbox FS, psutil network/users; X11 (wmctrl/xdotool) or Wayland (ydotool/wtype, wlrctl/swaymsg/hyprctl); clipboard; mss screenshots; pyatspi AT-SPI + **vision/OCR fallback** for canvas/games; pactl/wpctl audio; systemctl user/system services; gated privilege (`ADMIN` + `WINOS_ALLOW_PRIVILEGED`). Windows UIA is N/A on Linux by design.
- **FakeBackend**: Contoso CRM UI tree for adapter unit/E2E — `WINOS_BACKEND=fake` only.
- **WindowsBackend**: raises if instantiated off Win32; **real** UIA (`uiautomation` → `comtypes` → `pywinauto`), **SendInput** mouse/keyboard, **mss/Pillow/BitBlt** screenshots, EnumDisplayMonitors displays. Not a stub.
