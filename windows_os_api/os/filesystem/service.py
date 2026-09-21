"""Filesystem with sandbox policy.

N049 — il gate sta QUI, non solo nel backend. Read/write/delete/hash/stat
passano da ``paths.walk_under`` + open ``O_NOFOLLOW``: un symlink (anche
scambiato dopo la resolve) non e' un percorso valido, e' un rifiuto. Il
falso successo TOCTOU di Phase 0 (swap file→symlink verso ``/tmp`` fra
resolve e write) diventa ``PATH_SYMLINK_REFUSED`` / ``PATH_OUTSIDE_SANDBOX``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from windows_os_api.backends.factory import get_backend
from windows_os_api.os.filesystem import paths as fspaths


def _sandbox_root() -> Path:
    backend = get_backend()
    root = getattr(backend, "sandbox", None)
    if root is None:
        # Fake/legacy: chiedi al backend di esporre la root.
        getter = getattr(backend, "sandbox_root", None)
        if callable(getter):
            root = getter()
        else:
            root = Path("sandbox")
    return Path(root)


def _reject_or_wrap(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, fspaths.PathRejected):
        return {
            "ok": False,
            "error": str(exc),
            "code": exc.code,
        }
    if isinstance(exc, FileNotFoundError):
        return {"ok": False, "error": str(exc), "code": fspaths.PATH_NOT_FOUND}
    if isinstance(exc, PermissionError):
        return {
            "ok": False,
            "error": str(exc),
            "code": fspaths.PATH_OUTSIDE_SANDBOX,
        }
    raise exc


def list_dir(path: str = ".") -> list[dict[str, Any]] | dict[str, Any]:
    """Elenco directory sotto sandbox (N049: nessun follow di symlink)."""
    try:
        fspaths.normalize_user_path(path if path not in ("",) else ".")
        root = _sandbox_root()
        if path in (".", ""):
            target = root
        else:
            target = fspaths.walk_under(root, path)
        if not target.exists():
            return []
        if target.is_symlink():
            raise fspaths.PathRejected(
                f"symlink rifiutato: {path!r}", code=fspaths.PATH_SYMLINK_REFUSED
            )
        if not target.is_dir():
            raise NotADirectoryError(str(target))
        out: list[dict[str, Any]] = []
        with os_scandir_nofollow(target) as entries:
            for entry in entries:
                if entry.is_symlink():
                    # Visibile come symlink, non seguito.
                    out.append(
                        {
                            "name": entry.name,
                            "path": entry.name if path in (".", "") else f"{path.rstrip('/')}/{entry.name}",
                            "is_dir": False,
                            "is_symlink": True,
                            "size": 0,
                        }
                    )
                    continue
                out.append(
                    {
                        "name": entry.name,
                        "path": entry.name if path in (".", "") else f"{path.rstrip('/')}/{entry.name}",
                        "is_dir": entry.is_dir(follow_symlinks=False),
                        "is_symlink": False,
                        "size": entry.stat(follow_symlinks=False).st_size
                        if entry.is_file(follow_symlinks=False)
                        else 0,
                    }
                )
        return sorted(out, key=lambda x: x["name"])
    except (fspaths.PathRejected, PermissionError, FileNotFoundError, NotADirectoryError) as exc:
        return _reject_or_wrap(exc)


def os_scandir_nofollow(target: Path):
    import os

    return os.scandir(target)


def read_file(path: str, max_bytes: int = 65536) -> dict[str, Any]:
    try:
        root = _sandbox_root()
        target, data = fspaths.read_bytes_nofollow(root, path, max_bytes)
        import base64

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        return {
            "ok": True,
            "path": path,
            "size": len(data),
            "text": text,
            "base64": base64.b64encode(data).decode(),
            "abs_path": str(target),
        }
    except (fspaths.PathRejected, PermissionError, FileNotFoundError) as exc:
        return _reject_or_wrap(exc)


def write_file(path: str, content: str) -> dict[str, Any]:
    try:
        root = _sandbox_root()
        data = content.encode("utf-8")
        target = fspaths.write_bytes_nofollow(root, path, data)
        return {"ok": True, "path": path, "bytes": len(data), "abs_path": str(target)}
    except (fspaths.PathRejected, PermissionError, FileNotFoundError, IsADirectoryError) as exc:
        return _reject_or_wrap(exc)


def delete_file(path: str) -> dict[str, Any]:
    try:
        root = _sandbox_root()
        target = fspaths.unlink_nofollow(root, path)
        return {"ok": True, "path": path, "abs_path": str(target)}
    except (fspaths.PathRejected, PermissionError, FileNotFoundError, OSError) as exc:
        if isinstance(exc, (fspaths.PathRejected, PermissionError, FileNotFoundError)):
            return _reject_or_wrap(exc)
        return {"ok": False, "error": str(exc), "code": "FS_DELETE_FAILED"}


def stat_file(path: str) -> dict[str, Any]:
    """Metadati via lstat sotto sandbox (N049). Nessun follow."""
    try:
        root = _sandbox_root()
        info = fspaths.lstat_under(root, path)
        info["ok"] = True
        return info
    except (fspaths.PathRejected, PermissionError, FileNotFoundError) as exc:
        return _reject_or_wrap(exc)


def hash_file(path: str, algo: str = "sha256") -> dict[str, Any]:
    """Digest del file sotto sandbox (N049), letto senza seguire symlink."""
    try:
        root = _sandbox_root()
        info = fspaths.hash_under(root, path, algo=algo)
        info["ok"] = True
        return info
    except (fspaths.PathRejected, PermissionError, FileNotFoundError) as exc:
        return _reject_or_wrap(exc)
