"""N034 — Windows installer identity, key and least-privilege contracts.

Constants and pure helpers shared by packaging scripts, validate(), and unit
tests. No live SCM / icacls side effects here (those stay in shipped .bat/.ps1).
"""
from __future__ import annotations

from typing import Final

# Built-in least-privilege service account (not LocalSystem).
SERVICE_ACCOUNT: Final[str] = r"NT AUTHORITY\LocalService"

# Inno Setup single-instance mutex (wizard + setup).
SETUP_MUTEX: Final[str] = "WinOsApiSetupMutex"
APP_MUTEX: Final[str] = "WinOsApiAppMutex"

# Principals allowed to read api_key.txt after install (DACL, no inherited Everyone).
KEY_ACL_READ_PRINCIPALS: Final[tuple[str, ...]] = (
    r"NT AUTHORITY\SYSTEM",
    r"BUILTIN\Administrators",
    SERVICE_ACCOUNT,
)

# Principals needing Modify on runtime dirs (logs/tmp/sandbox) under {app}.
RUNTIME_DIR_ACL_MODIFY_PRINCIPALS: Final[tuple[str, ...]] = (
    r"NT AUTHORITY\SYSTEM",
    r"BUILTIN\Administrators",
    SERVICE_ACCOUNT,
)

# Well-known / documentation-only keys that must never authenticate a release install.
WEAK_API_KEYS: Final[frozenset[str]] = frozenset(
    {
        "dev",
        "admin",
        "test",
        "password",
        "secret",
        "changeme",
        "dev-key-change-me",
        "winos-dev",
        "winos-admin",
    }
)

SECURE_KEY_SCRIPT: Final[str] = "write_secure_api_key.ps1"
HARDEN_DIRS_SCRIPT: Final[str] = "harden_service_dirs.ps1"


def is_weak_api_key(value: str) -> bool:
    """True if *value* matches a denylisted weak/dev key (case-insensitive strip)."""
    return value.strip().lower() in WEAK_API_KEYS


def assert_release_api_key(value: str) -> str:
    """Return stripped key or raise ValueError if empty/weak."""
    key = value.strip()
    if not key:
        raise ValueError("API key must be non-empty")
    if is_weak_api_key(key):
        raise ValueError("API key matches a denylisted weak/dev value")
    return key
