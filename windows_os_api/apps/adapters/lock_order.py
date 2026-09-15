"""N045 — Lock order constants and action content fingerprints.

See ``lock_order.md`` in this package for the human-readable contract.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# Numeric levels mirror lock_order.md (acquire ascending only).
LOCK_LEVEL_REGISTRY = 1
LOCK_LEVEL_VERIFICATION = 2
LOCK_LEVEL_STORE_IO = 3

LOCK_ORDER = (
    ("registry", LOCK_LEVEL_REGISTRY, "_adapters_registry_lock"),
    ("verification", LOCK_LEVEL_VERIFICATION, "Adapter._verification_lock"),
    ("store_io", LOCK_LEVEL_STORE_IO, "store._io_lock"),
)


def action_content_fingerprint(action: Any) -> str:
    """Stable fingerprint of the action *identity* (not the verdict).

    VERIFIED evidence is valid only for this content. A changed automation_id,
    control_type, params, risk, or name is a different version — never keep a
    prior VERIFIED for it (H63-N045 / R22 R26 R33).
    """
    if isinstance(action, dict):
        name = action.get("name")
        automation_id = action.get("automation_id")
        control_type = action.get("control_type")
        params = action.get("params") or []
        risk = action.get("risk") or "low"
    else:
        name = getattr(action, "name", None)
        automation_id = getattr(action, "automation_id", None)
        control_type = getattr(action, "control_type", None)
        params = getattr(action, "params", None) or []
        risk = getattr(action, "risk", None) or "low"
    payload = {
        "name": name,
        "automation_id": automation_id,
        "control_type": control_type,
        "params": list(params),
        "risk": risk,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def verification_matches_action(verification: Any, action: Any) -> bool:
    """True unless a stamped ``action_fp`` disagrees with the action content.

    Missing ``action_fp`` is allowed for in-memory/legacy verdicts; ``store``
    migrates the stamp on load. A *present* mismatched stamp means VERIFIED of
    another action version and must not be published (N045).
    """
    if not isinstance(verification, dict):
        return False
    if verification.get("state") != "VERIFIED":
        return True  # non-VERIFIED: fingerprint gate does not apply
    stamped = verification.get("action_fp")
    if not (isinstance(stamped, str) and stamped.strip()):
        return True
    return stamped == action_content_fingerprint(action)
