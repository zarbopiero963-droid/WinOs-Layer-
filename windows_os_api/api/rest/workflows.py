"""Workflows, intent, agent, reasoning, healing, planner."""
from __future__ import annotations
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission
from windows_os_api.apps.workflows import recorder, generator
from windows_os_api.apps.adapters.engine import invoke_action
from windows_os_api.apps.intent.engine import execute_intent, parse_intent
from windows_os_api.apps.agent.computer import ComputerAgent
from windows_os_api.apps.reasoning.offline import reason
from windows_os_api.apps.healing.service import heal_selector
from windows_os_api.apps.planner.service import plan

router = APIRouter(tags=["automation"])

# `app_id` carries no default on any of these bodies. It used to default to
# "contoso-crm" — the fake backend's demo CRM — so a request that never named an
# application was accepted and applied to that one. Required here means FastAPI
# answers 422 and names the missing field, which is the honest reply to "do this"
# without "to what".
class RecordStart(BaseModel):
    name: str
    app_id: str

class RecordStep(BaseModel):
    action: str
    params: dict = {}

class IntentBody(BaseModel):
    text: str
    app_id: str

class AgentBody(BaseModel):
    goal: str
    app_id: str

class HealBody(BaseModel):
    hwnd: int = 1001
    automation_id: str
    fallback_name: str | None = None

class ReasonBody(BaseModel):
    query: str
    hwnd: int = 1001

@router.post("/workflows/record/start")
def start_rec(body: RecordStart, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_MANAGE))):
    wf = recorder.start_recording(body.name, body.app_id)
    return {"id": wf.id, "name": wf.name}

@router.post("/workflows/record/step")
def rec_step(body: RecordStep, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_MANAGE))):
    try:
        step = recorder.record_step(body.action, body.params)
        return {"action": step.action, "params": step.params}
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e

@router.post("/workflows/record/stop")
def stop_rec(auth: AuthContext = Depends(require_permission(Permission.ADAPTER_MANAGE))):
    wf = recorder.stop_recording()
    if not wf:
        raise HTTPException(400, "no active recording")
    return recorder.to_dict(wf)

@router.get("/workflows")
def list_wf(auth: AuthContext = Depends(require_permission(Permission.ADAPTER_USE))):
    return {"workflows": recorder.list_workflows()}

@router.post("/workflows/generate")
def gen_wf(body: IntentBody, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_MANAGE))):
    wf = generator.generate_workflow(body.text, app_id=body.app_id)
    return recorder.to_dict(wf)

@router.post("/workflows/{wf_id}/play")
def play_wf(wf_id: str, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_USE))):
    return recorder.play(wf_id, invoke_action)

@router.post("/intent")
def intent(body: IntentBody, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_USE))):
    return execute_intent(body.text, body.app_id)

@router.post("/intent/parse")
def intent_parse(body: IntentBody, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_USE))):
    return parse_intent(body.text)

@router.post("/agent/run")
def agent_run(body: AgentBody, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_USE))):
    return ComputerAgent(body.app_id).run(body.goal)

@router.post("/ui/reason")
def ui_reason(body: ReasonBody, auth: AuthContext = Depends(require_permission(Permission.UI_READ))):
    return reason(body.query, body.hwnd)

@router.post("/ui/heal")
def ui_heal(body: HealBody, auth: AuthContext = Depends(require_permission(Permission.UI_READ))):
    return heal_selector(body.hwnd, body.automation_id, body.fallback_name)

@router.post("/plan")
def make_plan(body: IntentBody, auth: AuthContext = Depends(require_permission(Permission.ADAPTER_USE))):
    return plan(body.text, body.app_id)
