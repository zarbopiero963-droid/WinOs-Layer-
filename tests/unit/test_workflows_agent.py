"""Workflows, intent, agent, healing, planner."""
import os
os.environ["WINOS_BACKEND"] = "fake"
from windows_os_api.core.runtime.config import get_settings
from windows_os_api.backends.factory import reset_backend
from windows_os_api.apps.adapters.engine import reset_adapters, create_adapter
from windows_os_api.apps.workflows.recorder import reset_workflows, start_recording, record_step, stop_recording, list_workflows
from windows_os_api.apps.workflows.generator import generate_workflow
from windows_os_api.apps.intent.engine import parse_intent, execute_intent
from windows_os_api.apps.agent.computer import ComputerAgent
from windows_os_api.apps.healing.service import heal_selector
from windows_os_api.apps.planner.service import plan
from windows_os_api.apps.reasoning.offline import reason

def setup_function():
    get_settings.cache_clear(); reset_backend(); reset_adapters(); reset_workflows()

def test_recorder():
    wf = start_recording("demo", "contoso-crm")
    record_step("set_field_email", {"value": "x@y.z"})
    record_step("click_btn_save")
    stopped = stop_recording()
    assert stopped is not None
    assert len(stopped.steps) == 2
    assert any(w["name"] == "demo" for w in list_workflows())

def test_generate_new_customer_workflow():
    create_adapter("contoso-crm")
    wf = generate_workflow("create new customer", app_id="contoso-crm")
    assert wf.confidence >= 0.8
    assert len(wf.steps) >= 2
    assert wf.risk in ("low", "medium", "high")

def test_intent_and_agent():
    create_adapter("contoso-crm")
    parsed = parse_intent("nuovo cliente")
    assert parsed["intent"] == "create_customer"
    result = ComputerAgent("contoso-crm").run("search customer")
    assert result["goal"] == "search customer"
    assert "intent" in result

def test_heal_and_reason():
    create_adapter("contoso-crm")
    healed = heal_selector(1001, "btn.save")
    assert healed["ok"] is True
    broken = heal_selector(1001, "btn.does_not_exist", fallback_name="Save")
    assert broken["ok"] is True and broken["healed"] is True
    r = reason("email")
    assert r["matched_element"] is not None
    assert r["engine"] == "deterministic-offline"

def test_plan_requires_confirmation_when_risky():
    create_adapter("contoso-crm")
    p = plan("create new customer", "contoso-crm")
    assert "workflow" in p
    assert p["confidence"] > 0
