"""Filesystem with sandbox policy."""
from __future__ import annotations

from typing import Any

from windows_os_api.backends.factory import get_backend


def _reject_traversal(path: str) -> None:
    if path in (".", ""):
        return
    norm = path.replace("\\", "/")
    parts = norm.split("/")
    if ".." in parts:
        raise PermissionError(f"Path traversal blocked: {path}")
    if ".." in path:
        raise PermissionError(f"Path traversal blocked: {path}")


def list_dir(path: str = ".") -> list[dict[str, Any]]:
    _reject_traversal(path)
    return get_backend().fs_list(path)


def read_file(path: str, max_bytes: int = 65536) -> dict[str, Any]:
    _reject_traversal(path)
    return get_backend().fs_read(path, max_bytes)


def write_file(path: str, content: str) -> dict[str, Any]:
    _reject_traversal(path)
    return get_backend().fs_write(path, content)


def delete_file(path: str) -> dict[str, Any]:
    _reject_traversal(path)
    return get_backend().fs_delete(path)
