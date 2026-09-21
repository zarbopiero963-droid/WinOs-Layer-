"""N030 — Sync adapter trust invalidate → API registry withdrawal.

When an adapter is tampered or trust-revoked, VERIFIED registry records for
that ``application_id`` must not remain exportable/executable.
"""
from __future__ import annotations

from typing import Any

from windows_os_api.apps.api_registry.model import (
    ApiStatus,
    get_api_registry,
)


def demote_verified_apis_for_app(
    application_id: str,
    *,
    reason: str = "adapter_trust_invalidated",
) -> list[str]:
    """Demote VERIFIED registry APIs for *application_id* to DISABLED.

    Clears ``verification_id`` / ``last_verified_at`` so OpenAPI/SDK/MCP export
    and gateway execute fail closed until a fresh verify+publish.
    Returns demoted api ids.
    """
    app = (application_id or "").strip()
    if not app:
        return []
    reg = get_api_registry()
    demoted: list[str] = []
    for rec in list(reg.list()):
        if (rec.application_id or "").strip() != app:
            continue
        if rec.status is not ApiStatus.VERIFIED:
            continue
        # Clear proof then set DISABLED (set_status re-gates VERIFIED).
        payload = rec.to_dict()
        payload["status"] = ApiStatus.DISABLED.value
        payload["verification_id"] = None
        payload["last_verified_at"] = None
        # Keep schemas/path; mark reason in description suffix only if empty reason field
        # Prefer register upsert for evidence clear.
        new_rec = reg.register(payload)
        # Ensure DISABLED even if register mapped oddly
        if new_rec.status is not ApiStatus.DISABLED:
            new_rec = reg.set_status(new_rec.id, ApiStatus.DISABLED)
        demoted.append(new_rec.id)
    return demoted
