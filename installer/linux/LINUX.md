# Linux portable package — WinOs-Layer

This artifact is the **FastAPI OS API server** built for Linux (PyInstaller
one-file ELF). By default it uses **`LinuxBackend`** (`WINOS_BACKEND=auto` /
`linux`): real processes, filesystem (sandbox), system info, and network via
psutil/subprocess — **not** an in-memory FakeBackend simulation.

Set `WINOS_BACKEND=fake` only when you need deterministic Contoso CRM adapter
fixtures for demos/CI.

**This is not a Windows emulator.** For real Windows automation
(`WindowsBackend`, UI Automation, Setup.exe / Windows service), download the
**Windows** artifacts (`dist-windows` / `winos-api-portable-windows.zip` /
`WinOsApi-Setup-*.exe`) instead.

## Real vs Fake

| Mode | Env | Behavior |
|------|-----|----------|
| Default | `auto` / `linux` | **LinuxBackend** — `POST /v1/processes` spawns real PIDs |
| Fixtures | `fake` | FakeBackend Contoso CRM UI tree for adapter E2E |

Optional tools (graceful if missing): `wmctrl`/`xdotool`, `xclip`/`xsel`/
`wl-clipboard`, `pactl`, `systemctl`, `mss`, `pyatspi`.

Registry API maps to `~/.config/winos-api/registry.json` for compatibility.

## Contents

- `winos-api` — portable binary
- `installer/linux/` — `install.sh`, `uninstall.sh`, `winos-api.service`
- `VERSION` — package version from `pyproject.toml`
- This `LINUX.md`

## Quick start

```bash
unzip winos-api-portable-linux.zip
cd winos-api-portable-linux   # or extracted root
chmod +x winos-api installer/linux/*.sh
./installer/linux/install.sh --user
# or run without install:
WINOS_BACKEND=auto ./winos-api serve --host 127.0.0.1 --port 8765
```

Default bind: **127.0.0.1:8765** (localhost only).

Smoke real process spawn:

```bash
python scripts/live_linux_smoke.py
```

## Choose your OS (GitHub Actions)

| Platform | Workflow | Artifact |
|----------|----------|----------|
| Linux portable | **Build Linux** or **Build** → `build-linux` | `dist-linux-portable` |
| Windows EXE/Setup | **Build** → `build-windows` | `dist-windows` |
| Both on tag | **Release** | GitHub Release assets |

See also `installer/README.md` → “Two builds — choose your OS”.
