"""Adapter sandbox permissions."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from windows_os_api.core.permissions.model import Permission

@dataclass
class SandboxPolicy:
    app_id: str
    allowed_actions: set[str] = field(default_factory=set)
    denied_actions: set[str] = field(default_factory=set)
    max_risk: str = "medium"  # low|medium|high
    require_permission: Permission = Permission.ADAPTER_USE

_policies: dict[str, SandboxPolicy] = {}
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

def set_policy(policy: SandboxPolicy) -> SandboxPolicy:
    _policies[policy.app_id] = policy
    return policy

def get_policy(app_id: str) -> SandboxPolicy:
    return _policies.get(app_id) or SandboxPolicy(app_id=app_id)

def check_action(app_id: str, action_name: str, risk: str = "low") -> dict[str, Any]:
    pol = get_policy(app_id)
    if action_name in pol.denied_actions:
        return {"allowed": False, "reason": "explicitly denied"}
    if pol.allowed_actions and action_name not in pol.allowed_actions:
        return {"allowed": False, "reason": "not in allowlist"}
    if _RISK_ORDER.get(risk, 99) > _RISK_ORDER.get(pol.max_risk, 1):
        return {"allowed": False, "reason": f"risk {risk} exceeds max {pol.max_risk}"}
    return {"allowed": True}

def reset_policies() -> None:
    _policies.clear()
