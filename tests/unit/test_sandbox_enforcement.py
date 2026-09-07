"""The adapter sandbox must gate EVERY path to the backend, not just the REST route.

`invoke_action` used to carry the comment "Permission / sandbox check delegated
to caller". Of its four call sites only `api/rest/apps.py` honoured that
delegation; `api/rest/workflows.py` (recorder.play), `api/mcp/server.py` — the
tool an AI agent drives — and `apps/agent/computer.py` reached the backend with
the policy unchecked. A policy set through `PUT /v1/sandbox/policy` was
therefore enforced on one surface out of four.

Four of the tests below drive a real surface with a denying policy in place and
fail on the old code: the direct entry point, the risk ceiling, the MCP tool and
workflow playback. The other two are deliberately not regression tests — one is
a positive control (an allowed action must still run, so the gate cannot pass by
denying everything), the other is a structural check on the agent, explained at
its own docstring.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("WINOS_BACKEND", "fake")

from windows_os_api.api.mcp.server import call_tool
from windows_os_api.apps.adapters.engine import create_adapter, invoke_action, reset_adapters
from windows_os_api.apps.sandbox.permissions import SandboxPolicy, reset_policies, set_policy
from windows_os_api.apps.workflows import recorder
from windows_os_api.backends.factory import reset_backend
from windows_os_api.core.runtime.config import get_settings

APP = "contoso-crm"


def setup_function():
    get_settings.cache_clear()
    reset_backend()
    reset_adapters()
    reset_policies()


def _adapter_and_action(control_type: str = "Button") -> str:
    """Create the CRM adapter and return the name of one real action."""
    adapter = create_adapter(APP, hwnd=1001)
    action = next(a for a in adapter.actions if a.control_type == control_type)
    return action.name


def _deny(action_name: str) -> None:
    set_policy(SandboxPolicy(app_id=APP, denied_actions={action_name}))


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------
def test_invoke_action_refuses_a_denied_action(tmp_sandbox):
    action = _adapter_and_action()
    _deny(action)

    result = invoke_action(APP, action)

    assert result["ok"] is False
    assert result["denied"] is True
    assert "denied" in result["error"]


def test_invoke_action_still_runs_an_allowed_action(tmp_sandbox):
    """The gate must not deny by accident — a permitted action still executes."""
    action = _adapter_and_action()
    set_policy(SandboxPolicy(app_id=APP, denied_actions={"some_other_action"}))

    result = invoke_action(APP, action)

    assert result.get("denied") is not True
    assert result.get("error") != "explicitly denied"


def test_invoke_action_enforces_the_risk_ceiling(tmp_sandbox):
    """A policy capped at low risk must block a medium-risk action."""
    adapter = create_adapter(APP, hwnd=1001)
    risky = next(a for a in adapter.actions if a.risk != "low")
    set_policy(SandboxPolicy(app_id=APP, max_risk="low"))

    result = invoke_action(APP, risky.name)

    assert result["ok"] is False
    assert result["denied"] is True
    assert "risk" in result["error"]


# ---------------------------------------------------------------------------
# The three surfaces that used to bypass it
# ---------------------------------------------------------------------------
def test_mcp_tool_cannot_bypass_the_policy(tmp_sandbox):
    """api/mcp/server.py — the surface an AI agent drives. Previously ungated."""
    action = _adapter_and_action()
    _deny(action)

    result = call_tool("invoke_action", {"app_id": APP, "action": action})

    assert result["ok"] is False
    assert result["denied"] is True


def test_agent_reaches_actions_only_through_the_gated_entry_point(tmp_sandbox):
    """apps/agent/computer.py must go through invoke_action, never round the back.

    An end-to-end test of the agent cannot prove this today: the planner sets
    requires_confirmation when risk is medium/high OR confidence < 0.8, and on
    the CRM fixture confidence tops out at 0.75 for every goal — so the agent
    plans and executes nothing. An assertion over its (always empty) executed
    list would be vacuously true, which is worse than no test at all.

    What IS checkable, and what actually matters, is the structural invariant:
    the agent touches the backend only via invoke_action, where the gate now
    lives. If someone later wires it straight to a backend, this fails.
    """
    source = (
        Path(__file__).resolve().parents[2] / "windows_os_api" / "apps" / "agent" / "computer.py"
    ).read_text(encoding="utf-8")

    assert "invoke_action(" in source, "the agent must dispatch through invoke_action"
    assert "get_backend" not in source, "the agent must not reach the backend directly"
    assert "type_text" not in source and "click_element" not in source


def test_workflow_playback_cannot_bypass_the_policy(tmp_sandbox):
    """api/rest/workflows.py plays recorded steps through invoke_action. Previously ungated."""
    action = _adapter_and_action()
    recorder.reset_workflows()
    wf = recorder.start_recording("denied-playback", app_id=APP)
    recorder.record_step(action, {})
    recorder.stop_recording()
    _deny(action)

    played = recorder.play(wf.id, invoke_action)

    steps = played.get("results") or played.get("steps") or []
    assert steps, played
    assert all(
        (s.get("result") or s).get("denied") is True for s in steps
    ), played
