# Installer packaging — WinOs-Layer

Produces a portable `winos-api` binary (PyInstaller) and optional Windows
`Setup.exe` (Inno Setup). Default bind is **127.0.0.1:8765** (localhost only).

## Service name

Windows service: **`WindowsOSLayerService`**  
CLI entry: **`winos-api`** (`windows_os_api.cli.main:main`)

## Layout

```
installer/
  pyinstaller/winos-api.spec   # one-file EXE/ELF
  inno/winos-api.iss           # Inno Setup script → Setup.exe
  service_scripts/             # NSSM / sc.exe install+uninstall
  README.md                    # this file
scripts/build_installer.py     # validate | build-portable | build-installer | checksums
```

## Local helper

```bash
# Consistency check (Linux + Windows)
python scripts/build_installer.py validate

# Dry-run (no build)
python scripts/build_installer.py --dry-run build-portable
python scripts/build_installer.py --dry-run build-installer

# Portable one-file (current OS — Linux smoke ELF or Windows .exe)
pip install pyinstaller
python scripts/build_installer.py build-portable

# SHA-256 of dist/ artifacts
python scripts/build_installer.py checksums
```

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
- `installer/output/WinOsApi-Setup-1.0.0.exe` — Inno installer
- `dist/checksums.txt` / `installer/output/checksums.txt`

## What the installer does

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

See `.github/workflows/build.yml` and `release.yml`:
- `ubuntu-latest`: wheel + `build_installer.py validate` + optional Linux portable smoke
- `windows-latest`: PyInstaller portable zip + Inno Setup.exe + sha256 artifacts
