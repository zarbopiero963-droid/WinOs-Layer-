"""Windows service installer helpers (scripts + module)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

SERVICE_NAME = "WinOsApi"
SERVICE_DISPLAY = "Windows OS API Layer"
NSSM_TEMPLATE = '''@echo off
REM Install WinOsApi as a Windows service via NSSM (run as Administrator)
set NSSM=%~dp0nssm.exe
set APP=%~dp0winos-api.exe
"%NSSM%" install {name} "%APP%" serve --host 127.0.0.1 --port 8765
"%NSSM%" set {name} AppDirectory "%~dp0"
"%NSSM%" set {name} DisplayName "{display}"
"%NSSM%" set {name} Start SERVICE_AUTO_START
"%NSSM%" start {name}
echo Installed {name}
'''

SC_TEMPLATE = '''@echo off
REM Alternative sc.exe create (requires absolute path to python/exe)
sc create {name} binPath= "\\"{bin}\\" serve --host 127.0.0.1 --port 8765" start= auto DisplayName= "{display}"
sc description {name} "FastAPI Windows OS API Layer — localhost only"
sc start {name}
'''


def generate_install_scripts(output_dir: str | Path, binary_path: str = "winos-api.exe") -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    nssm = NSSM_TEMPLATE.format(name=SERVICE_NAME, display=SERVICE_DISPLAY)
    sc = SC_TEMPLATE.format(name=SERVICE_NAME, display=SERVICE_DISPLAY, bin=binary_path)
    uninstall = f'@echo off\nsc stop {SERVICE_NAME}\nsc delete {SERVICE_NAME}\necho Removed {SERVICE_NAME}\n'
    paths = {
        "install_nssm.bat": nssm,
        "install_sc.bat": sc,
        "uninstall_service.bat": uninstall,
    }
    written = {}
    for name, content in paths.items():
        p = out / name
        p.write_text(content, encoding="utf-8")
        written[name] = str(p)
    return written


def service_manifest() -> dict[str, Any]:
    return {
        "name": SERVICE_NAME,
        "display_name": SERVICE_DISPLAY,
        "bind": "127.0.0.1:8765",
        "remote_access": False,
    }
