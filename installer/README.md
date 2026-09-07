# Installer packaging — WinOs-Layer

Produces a portable `winos-api` binary (PyInstaller) and optional Windows
`Setup.exe` (Inno Setup). Default bind is **127.0.0.1:8765** (localhost only).

## Two builds — choose your OS

| What you need | Workflow | Artifact | What it is |
|---------------|----------|----------|------------|
| **Linux portable** | Actions → **Build Linux** (or **Build** → `build-linux`) | `dist-linux-portable` (`winos-api-portable-linux.zip`) | FastAPI server ELF; uses **FakeBackend** / OS-portable API on non-Windows hosts. **Not a Windows emulator.** |
| **Windows EXE/Setup** | Actions → **Build** → `build-windows` | `dist-windows` (`winos-api-portable-windows.zip` + `WinOsApi-Setup-*.exe`) | Real **WindowsBackend** when running on Win32; Windows service scripts. |
| **Both on a tag** | Actions → **Release** | GitHub Release assets | Attaches Windows exe/zip **and** Linux zip + checksums. |

Also: `dist-linux-python` = wheel/sdist from `python -m build`.

```
Actions → Build Linux  → artifact dist-linux-portable
Actions → Build        → dist-windows (Setup.exe + portable zip)
                       → dist-linux-portable (same zip naming via package-linux)
Release                → attaches both Windows + Linux to the GitHub Release
```

## Service name

Windows service: **`WindowsOSLayerService`**  
CLI entry: **`winos-api`** (`windows_os_api.cli.main:main`)  
Linux systemd unit: **`winos-api.service`** (see `installer/linux/`)

## Layout

```
installer/
  pyinstaller/winos-api.spec   # one-file EXE/ELF
  inno/winos-api.iss           # Inno Setup script → Setup.exe
  service_scripts/             # NSSM / sc.exe install+uninstall (Windows)
  linux/                       # systemd unit + install/uninstall (Linux)
    winos-api.service
    install.sh
    uninstall.sh
    LINUX.md
  README.md                    # this file
scripts/build_installer.py     # validate | build-portable | package-linux | …
```

## Local helper

```bash
# Consistency check (Linux + Windows)
python scripts/build_installer.py validate

# Dry-run (no build)
python scripts/build_installer.py --dry-run build-portable
python scripts/build_installer.py --dry-run build-installer
python scripts/build_installer.py --dry-run package-linux

# Portable one-file (current OS — Linux smoke ELF or Windows .exe)
pip install pyinstaller
python scripts/build_installer.py build-portable

# Linux portable zip/tar.gz (requires dist/winos-api, or add --build)
python scripts/build_installer.py package-linux
# or: python scripts/build_installer.py package-linux --build

# SHA-256 of dist/ artifacts
python scripts/build_installer.py checksums
```

## Linux portable (FakeBackend-capable)

```bash
python scripts/build_installer.py build-portable
python scripts/build_installer.py package-linux
# → dist/winos-api-portable-linux.zip (+ .tar.gz) + checksums-linux.txt
unzip dist/winos-api-portable-linux.zip
./winos-api-portable-linux/installer/linux/install.sh --user
```

The Linux package runs **FakeBackend** / OS-portable API server for non-Windows
hosts. The Windows EXE uses **WindowsBackend** when on Win32.

## Windows Setup.exe (requires Inno Setup)

`Setup.exe` **cannot** be produced on Linux. On Windows (or GHA `windows-latest`):

```bat
choco install innosetup -y
pip install -e ".[dev,windows]" pyinstaller
python scripts/build_installer.py build-portable
python scripts/build_installer.py build-installer
python scripts/build_installer.py checksums
```

Artifacts:
- `dist/winos-api.exe` — portable
- `dist/winos-api-portable-windows.zip` — portable + service scripts
- `installer/output/WinOsApi-Setup-1.0.0.exe` — Inno installer
- `dist/checksums.txt` / `installer/output/checksums.txt`

## What the Windows installer does

1. Copies `winos-api.exe` under `{autopf}\WinOsApi`
2. Copies `installer/service_scripts\*.bat` to `{app}\service`
3. Generates a random API key into `{app}\api_key.txt` (first install)
4. Offers to start the API bound to `127.0.0.1:8765`
5. Optional: run `service\install_nssm.bat` as Administrator to register
   **WindowsOSLayerService**

## Firewall / remote access

Default is **localhost only**. Do not open firewall ports unless you deliberately
enable remote access (`WINOS_REMOTE_ACCESS=true`) and understand the risk.

## CI

See `.github/workflows/`:
- `build-linux.yml` — dedicated Linux portable zip + wheel
- `build.yml` — `build-linux` (package-linux) + `build-windows` (Setup.exe)
- `release.yml` — Windows + Linux artifacts attached to GitHub Release
- `ci.yml` — validate + pytest
