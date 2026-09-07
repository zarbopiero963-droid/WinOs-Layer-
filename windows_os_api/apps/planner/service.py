"""Adapter planner — plan multi-step UI sequences."""
from __future__ import annotations
from typing import Any
from windows_os_api.apps.workflows.generator import generate_workflow
from windows_os_api.apps.workflows.recorder import to_dict

def plan(goal: str, app_id: str = "contoso-crm") -> dict[str, Any]:
    wf = generate_workflow(goal, app_id=app_id)
    return {
        "goal": goal,
        "app_id": app_id,
        "workflow": to_dict(wf),
        "confidence": wf.confidence,
        "risk": wf.risk,
        "requires_confirmation": wf.risk in ("medium", "high") or wf.confidence < 0.8,
    }
