"""Computer agent — high-level orchestrator over OS + adapters.

It plans, and it may act — but only when an explicit gate says it may. See
`apps/agent/gate.py` for the rule and issue #6 for the decision behind it.

What changed, and why it mattered
---------------------------------
The execution branch used to be guarded by `if not plan["requires_confirmation"]`
alone. That condition happens to be true for every workflow the current
generator produces, so the branch never ran — and nothing said so. A dead branch
that looks live is worse than either a live one or none at all: it was found
because a test asserting things about `executed` passed while asserting nothing,
`executed` being permanently `[]`.

The gate does not make the agent more permissive. It makes the same outcome
*declared*: `did_not_execute_because` names the condition that stopped it, so
"the agent planned and did not act" is an answer rather than an absence.
"""
from __future__ import annotations
from typing import Any
from windows_os_api.apps.intent.engine import execute_intent
from windows_os_api.apps.adapters.engine import create_adapter, invoke_action, get_adapter
from windows_os_api.apps.agent.gate import evaluate
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
        workflow = plan.get("workflow") or {}

        decision = evaluate(plan, self.app_id)
        executed: list[dict[str, Any]] = []
        if decision.execute:
            for step in workflow.get("steps") or []:
                # `invoke_action` checks the sandbox itself and is the actual
                # enforcement point (#13). The gate above decided whether to
                # try; this decides whether it happens. If the two ever
                # disagree, the refusal here wins — which is the whole reason
                # the AI layer is not the boundary.
                executed.append(
                    invoke_action(self.app_id, step["action"], step.get("params"))
                )

        response: dict[str, Any] = {
            "goal": goal,
            "intent": result["parsed"],
            "plan": plan,
            "executed": executed,
            "gate": decision.as_dict(),
            "status": "completed" if executed else "planned",
        }
        if not decision.execute:
            # Named, not merely absent: a caller must be able to tell "unsure"
            # from "too risky" from "the policy said no".
            response["did_not_execute_because"] = decision.reason
        return response
