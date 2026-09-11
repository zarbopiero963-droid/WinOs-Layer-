# Installer packaging — WinOs-Layer

Produces a portable `winos-api` binary (PyInstaller) and optional Windows
`Setup.exe` (Inno Setup). Default bind is **127.0.0.1:8765** (localhost only).

## Two builds — choose your OS

| What you need | Workflow | Artifact | What it is |
|---------------|----------|----------|------------|
| **Linux portable** | Actions → **Build Linux** (or **Build** → `build-linux`) | `dist-linux-portable` (`winos-api-portable-linux.zip`) | FastAPI server ELF; **LinuxBackend** (real OS) by default; Fake optional. **Not a Windows emulator.** |
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
  service_scripts/             # NSSM install/uninstall + unsafe sc.exe guard
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

## Linux portable (LinuxBackend real OS)

```bash
python scripts/build_installer.py build-portable
python scripts/build_installer.py package-linux
# → dist/winos-api-portable-linux.zip (+ .tar.gz) + checksums-linux.txt
unzip dist/winos-api-portable-linux.zip
./winos-api-portable-linux/installer/linux/install.sh --user
```

The Linux package runs **LinuxBackend** (real processes/FS/system) by default; use `WINOS_BACKEND=fake` for Contoso fixtures. Non-Windows
hosts. The Windows EXE uses **WindowsBackend** when on Win32.

## Windows Setup.exe (requires Inno Setup)

`Setup.exe` **cannot** be produced on Linux. On Windows (or GHA `windows-latest`):

```powershell
choco install innosetup -y
choco install nssm --version 2.24.101.20180116 -y
$nssm = Get-ChildItem "$env:ChocolateyInstall\lib\nssm*\tools" -Filter nssm.exe -Recurse -File |
  Select-Object -First 1
if (-not $nssm) { throw "Native NSSM package payload not found" }
Copy-Item $nssm.FullName installer\service_scripts\nssm.exe -Force
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

## Windows service lifecycle

Official Windows artifacts include the native [NSSM](https://nssm.cc/) 2.24
binary beside `install_nssm.bat`. A source-tree installation must place the
real native `nssm.exe` there itself; a package-manager shim from `PATH` is not a
valid service host. Then run the batch file as Administrator. It:

- resolves both the Setup layout (`service\` below the EXE) and portable layout;
- requires a one-line `api_key.txt` beside `winos-api.exe`;
- binds only to `127.0.0.1` and reads the key with `--api-key-file`, so the
  secret is not stored in the service command line;
- sends `CTRL_C_EVENT` first on stop, waits up to 15 seconds for graceful
  uvicorn shutdown, and applies the stop to the complete PyInstaller process
  tree;
- fails closed and rolls back a partial service registration.

Console output is written to `logs\service.log` for startup diagnosis. The
product uninstaller removes that directory with the other runtime logs.

`WINOS_SERVICE_PORT` may select another port (1–65535); the default is `8765`.
Run `service\uninstall_service.bat` as Administrator to stop, wait for
`STOPPED`, delete the service, and verify that SCM no longer lists it.

Do not use direct `sc create` with `winos-api.exe`: it is a console executable,
not a native Windows `ServiceMain` binary. The shipped `install_sc.bat` refuses
that invalid registration and directs users to NSSM.

## Firewall / remote access

Default is **localhost only**. Do not open firewall ports unless you deliberately
enable remote access (`WINOS_REMOTE_ACCESS=true`) and understand the risk.

## CI

See `.github/workflows/`:
- `build-linux.yml` — dedicated Linux portable zip + wheel
- `build.yml` — `build-linux` (package-linux) + `build-windows` (Setup.exe)
- `release.yml` — Windows + Linux artifacts attached to GitHub Release
- `ci.yml` — validate + pytest

The Windows build performs a hard service smoke against the installed frozen
EXE: real NSSM/SCM start, authenticated loopback HTTP, stop, restart and
uninstall. It requires a durable `server.shutdown` event, zero surviving
`winos-api.exe` processes and a released TCP port after every stop.
