"""Auto workflow generation with confidence / risk / rollback."""
from __future__ import annotations

import uuid

from windows_os_api.apps.adapters.engine import create_adapter, get_adapter
from windows_os_api.apps.semantic.mapper import map_intent_to_element
from windows_os_api.apps.ui_inspector.service import get_tree
from windows_os_api.apps.workflows.recorder import Workflow, WorkflowStep, _workflows


def generate_workflow(intent: str, app_id: str, hwnd: int = 1001) -> Workflow:
    tree = get_tree(hwnd)
    adapter = get_adapter(app_id) or create_adapter(app_id, hwnd)
    steps: list[WorkflowStep] = []
    rollback: list[WorkflowStep] = []
    confidence = 0.5
    risk = "low"

    intent_l = intent.lower()
    # Pattern: create/save customer
    if any(k in intent_l for k in ("new customer", "nuovo cliente", "create customer", "add customer")):
        # Fill fields then save
        for field_aid, action_prefix in [
            ("field.customer_name", "set_field_customer_name"),
            ("field.email", "set_field_email"),
            ("field.phone", "set_field_phone"),
        ]:
            act = next((a for a in adapter.actions if a.automation_id == field_aid), None)
            if act:
                steps.append(WorkflowStep(action=act.name, params={"value": ""}))
        save = next((a for a in adapter.actions if "save" in a.name), None)
        if save:
            steps.append(WorkflowStep(action=save.name, params={}))
            rollback.append(WorkflowStep(action="menu_menu_file_exit", params={}))
        confidence = 0.9
        risk = "medium"
    else:
        el = map_intent_to_element(tree, intent)
        if el and el.get("automation_id"):
            act = next((a for a in adapter.actions if a.automation_id == el["automation_id"]), None)
            if act:
                steps.append(
                    WorkflowStep(
                        action=act.name,
                        params={name: "" for name in act.params},
                    )
                )
                confidence = 0.75
                risk = act.risk

    wf = Workflow(
        id=str(uuid.uuid4()),
        name=f"auto:{intent[:40]}",
        steps=steps,
        app_id=app_id,
        confidence=confidence,
        risk=risk,
        rollback=rollback,
    )
    _workflows[wf.id] = wf
    return wf
