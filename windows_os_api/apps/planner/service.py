"""Adapter planner — plan multi-step UI sequences (+ optional AI assist)."""
from __future__ import annotations
from typing import Any
from windows_os_api.apps.workflows.generator import generate_workflow
from windows_os_api.apps.workflows.recorder import to_dict

def plan(goal: str, app_id: str) -> dict[str, Any]:
    wf = generate_workflow(goal, app_id=app_id)
    ai_hint = None
    engine = "deterministic"
    try:
        from windows_os_api.apps.ai.settings_store import get_ai_settings
        from windows_os_api.apps.ai.provider import get_ai_client
        s = get_ai_settings()
        if s.remote_ready():
            client = get_ai_client()
            prompt = (
                f"Plan UI automation steps for goal: {goal!r} in app {app_id}. "
                "Reply briefly with numbered steps."
            )
            ai_hint = client.complete(prompt) or None
            if ai_hint:
                engine = f"hybrid:{s.provider}"
            else:
                engine = f"deterministic+{s.provider}-ready"
    except Exception:  # noqa: BLE001
        ai_hint = None
    return {
        "goal": goal,
        "app_id": app_id,
        "workflow": to_dict(wf),
        "confidence": wf.confidence,
        "risk": wf.risk,
        "requires_confirmation": wf.risk in ("medium", "high") or wf.confidence < 0.8,
        "ai_hint": ai_hint,
        "engine": engine,
    }
