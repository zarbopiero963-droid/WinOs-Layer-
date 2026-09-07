import zipfile
from pathlib import Path
root = Path("/workspace/WinOs-Layer-")
out = Path("/workspace/WinOs-Layer-code.zip")
exclude = {
    ".venv", ".pytest_cache", "windows_os_api.egg-info", "__pycache__",
    "dist", "build", "logs", ".git", "sandbox",
}
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        parts = p.relative_to(root).parts
        if any(x in exclude for x in parts) or p.suffix == ".pyc":
            continue
        z.write(p, str(Path("WinOs-Layer-") / p.relative_to(root)))
print(out, out.stat().st_size)
