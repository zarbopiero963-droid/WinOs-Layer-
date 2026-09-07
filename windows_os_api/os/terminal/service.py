"""Terminal execute with ALLOW|DENY|ADMIN policy."""
from __future__ import annotations

from typing import Any

from windows_os_api.backends.factory import get_backend

ALLOWED_POLICIES = frozenset({"ALLOW", "DENY", "ADMIN"})


def execute(command: str, policy: str = "ALLOW") -> dict[str, Any]:
    policy = policy.upper()
    if policy not in ALLOWED_POLICIES:
        return {"ok": False, "error": f"policy must be one of {sorted(ALLOWED_POLICIES)}"}
    # Block obvious injection patterns when policy is ALLOW
    dangerous = [";", "&&", "|", "`", "$(", "\n"]
    if policy == "ALLOW" and any(d in command for d in dangerous):
        return {"ok": False, "policy": "DENY", "error": "shell metacharacters blocked under ALLOW"}
    return get_backend().terminal_execute(command, policy)
