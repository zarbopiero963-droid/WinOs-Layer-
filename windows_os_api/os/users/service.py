"""Users and sessions."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend
from windows_os_api.os.capability import discover

def list_users() -> dict[str, Any]:
    """Utenti locali, col contratto `supported` (D3 / N004).

    Prima restituiva una lista bare: `[]` era indistinguibile da unsupported
    o discovery fallita. La chiave `users` resta dov'era — additivo.
    """
    b = get_backend()
    return discover(b, "users", "users", b.list_users)

def list_sessions() -> dict[str, Any]:
    """Sessioni, col contratto `supported`.

    Senza, un backend privo di `win32ts` risponderebbe `[]` — indistinguibile da
    «nessuno e' connesso», che su una macchina in uso e' falso. La chiave
    `sessions` resta dov'era: additivo.
    """
    b = get_backend()
    return discover(b, "sessions", "sessions", b.list_sessions)

def session_info() -> dict[str, Any]:
    b = get_backend()
    if hasattr(b, "session_info"):
        return b.session_info()
    return {
        "sessions": b.list_sessions(),
        "users": b.list_users(),
        "session": {"session_type": "unknown"},
    }
