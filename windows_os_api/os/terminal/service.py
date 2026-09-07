"""Terminal execute with ALLOW|DENY|ADMIN policy.

The command allowlist itself is enforced in the backends, at the point that
actually executes — see `windows_os_api/os/terminal/allowlist.py`. This layer
only validates the policy name and delegates, so there is exactly one place
where "may this command run?" is decided and no second copy to drift out of
sync with it.
"""
from __future__ import annotations

from typing import Any

from windows_os_api.backends.factory import get_backend

ALLOWED_POLICIES = frozenset({"ALLOW", "DENY", "ADMIN"})


def execute(command: str, policy: str = "ALLOW") -> dict[str, Any]:
    policy = policy.upper()
    if policy not in ALLOWED_POLICIES:
        return {"ok": False, "error": f"policy must be one of {sorted(ALLOWED_POLICIES)}"}
    return get_backend().terminal_execute(command, policy)
