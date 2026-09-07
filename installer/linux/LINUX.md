# Linux portable package — WinOs-Layer

This artifact is the **FastAPI OS API server** built for Linux (PyInstaller
one-file ELF). On non-Windows hosts it uses **`FakeBackend`** (or `auto`),
providing an OS-portable API for development, CI, and adapters that do not
require real Win32 COM/UIA.

**This is not a Windows emulator.** For real Windows automation
(`WindowsBackend`, UI Automation, Setup.exe / Windows service), download the
**Windows** artifacts (`dist-windows` / `winos-api-portable-windows.zip` /
`WinOsApi-Setup-*.exe`) instead.

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
WINOS_BACKEND=fake ./winos-api serve --host 127.0.0.1 --port 8765
```

Default bind: **127.0.0.1:8765** (localhost only).

## Choose your OS (GitHub Actions)

| Platform | Workflow | Artifact |
|----------|----------|----------|
| Linux portable | **Build Linux** or **Build** → `build-linux` | `dist-linux-portable` |
| Windows EXE/Setup | **Build** → `build-windows` | `dist-windows` |
| Both on tag | **Release** | GitHub Release assets |

See also `installer/README.md` → “Two builds — choose your OS”.
