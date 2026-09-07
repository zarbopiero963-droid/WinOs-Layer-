"""Computer agent — high-level orchestrator over OS + adapters."""
from __future__ import annotations
from typing import Any
from windows_os_api.apps.intent.engine import execute_intent
from windows_os_api.apps.adapters.engine import create_adapter, invoke_action, get_adapter
from windows_os_api.core.events.bus import get_event_bus, Event

class ComputerAgent:
    def __init__(self, app_id: str = "contoso-crm") -> None:
        self.app_id = app_id
        if not get_adapter(app_id):
            create_adapter(app_id)

    def run(self, goal: str) -> dict[str, Any]:
        result = execute_intent(goal, app_id=self.app_id)
        get_event_bus().publish_sync("agent.goal", {"goal": goal, "app_id": self.app_id})
        plan = result["plan"]
        wf = plan.get("workflow") or {}
        executed = []
        if not plan.get("requires_confirmation"):
            for step in wf.get("steps") or []:
                executed.append(invoke_action(self.app_id, step["action"], step.get("params")))
        return {
            "goal": goal,
            "intent": result["parsed"],
            "plan": plan,
            "executed": executed,
            "status": "completed" if executed else "planned",
        }
