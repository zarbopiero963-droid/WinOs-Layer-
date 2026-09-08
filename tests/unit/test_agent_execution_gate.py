"""When may the agent act on its own plan?

Owner decision, issue #6: keep the execution path, make it explicit and
impossible to trip by accident.

    confidence >= 0.8  AND  risk == low  AND  security gate == ALLOW
                                ↓
                    invoke_action → sandbox → backend → audit

The two halves this file keeps apart:

* the **gate** decides whether to attempt, and names the condition that stopped
  it — that is honesty, and it is allowed to be wrong;
* `invoke_action` **enforces** the sandbox (#13) — that is the boundary, and it
  is not allowed to be wrong.

`test_the_sandbox_still_refuses_when_the_gate_wrongly_says_execute` is the one
that matters most: it proves the AI layer is not the defence.
"""
from __future__ import annotations

import pytest

from windows_os_api.apps.agent import computer as agent_module
from windows_os_api.apps.agent.computer import ComputerAgent
from windows_os_api.apps.agent.gate import (
    ALLOWED_RISK,
    MIN_CONFIDENCE,
    REASON_CONFIDENCE,
    REASON_CONFIRMATION,
    REASON_NO_STEPS,
    REASON_POLICY,
    REASON_RISK,
    GateDecision,
    evaluate,
)
from windows_os_api.apps.sandbox.permissions import SandboxPolicy, set_policy


def qualifying_plan(**overrides):
    """A plan that satisfies every condition — the ALLOW case, spelled out."""
    plan = {
        "confidence": 0.9,
        "risk": "low",
        "requires_confirmation": False,
        "workflow": {"steps": [{"action": "click_btn_search", "params": {}}]},
    }
    plan.update(overrides)
    return plan


# ---------------------------------------------------------------------------
# The gate in isolation
# ---------------------------------------------------------------------------
def test_a_plan_meeting_every_condition_is_allowed(tmp_sandbox):
    decision = evaluate(qualifying_plan(), "contoso-crm")
    assert decision.execute is True, decision
    assert decision.reason is None


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"confidence": 0.79}, REASON_CONFIDENCE),
        ({"confidence": 0.5}, REASON_CONFIDENCE),
        ({"confidence": None}, REASON_CONFIDENCE),
        ({"confidence": "0.9"}, REASON_CONFIDENCE),
        ({"risk": "medium"}, REASON_RISK),
        ({"risk": "high"}, REASON_RISK),
        ({"risk": None}, REASON_RISK),
        ({"requires_confirmation": True}, REASON_CONFIRMATION),
        ({"workflow": {"steps": []}}, REASON_NO_STEPS),
        ({"workflow": {}}, REASON_NO_STEPS),
    ],
)
def test_one_missing_condition_is_enough_to_refuse(tmp_sandbox, overrides, expected):
    """Every condition alone can stop it, and the reason says which one.

    Returning a bare False would leave the caller to guess between "unsure",
    "too risky" and "the policy said no" — three situations with three
    different answers.
    """
    decision = evaluate(qualifying_plan(**overrides), "contoso-crm")
    assert decision.execute is False, decision
    assert decision.reason == expected, decision
    assert decision.detail, "a refusal must explain itself"


def test_the_threshold_is_exactly_at_the_boundary(tmp_sandbox):
    """0.8 passes, 0.7999 does not — pinned so the constant cannot drift quietly."""
    assert evaluate(qualifying_plan(confidence=MIN_CONFIDENCE), "contoso-crm").execute is True
    assert evaluate(qualifying_plan(confidence=MIN_CONFIDENCE - 0.0001),
                    "contoso-crm").execute is False


def test_high_confidence_does_not_excuse_a_risky_action(tmp_sandbox):
    """Both axes, not either.

    Being certain about something destructive is precisely the case that needs a
    human, so confidence cannot buy its way past risk.
    """
    decision = evaluate(qualifying_plan(confidence=1.0, risk="high"), "contoso-crm")
    assert decision.execute is False
    assert decision.reason == REASON_RISK


def test_the_sandbox_policy_can_refuse_a_plan_that_passes_everything_else(tmp_sandbox):
    """The BLOCK case: confidence and risk are fine, the policy is not."""
    set_policy(SandboxPolicy(app_id="contoso-crm", denied_actions=["click_btn_search"]))
    decision = evaluate(qualifying_plan(), "contoso-crm")
    assert decision.execute is False, decision
    assert decision.reason == REASON_POLICY, decision
    assert "click_btn_search" in decision.detail


def test_every_step_is_checked_not_only_the_first(tmp_sandbox):
    """A plan is only as safe as its most dangerous action.

    Stopping at the first allowed step would let everything after it through —
    and the denied action is deliberately placed last here so that a
    short-circuiting implementation passes the earlier assertions and fails
    this one.
    """
    set_policy(SandboxPolicy(app_id="contoso-crm", denied_actions=["click_btn_save"]))
    plan = qualifying_plan(workflow={"steps": [
        {"action": "click_btn_search", "params": {}},
        {"action": "set_field_email", "params": {}},
        {"action": "click_btn_save", "params": {}},
    ]})
    decision = evaluate(plan, "contoso-crm")
    assert decision.execute is False, decision
    assert decision.reason == REASON_POLICY
    assert "click_btn_save" in decision.detail


# ---------------------------------------------------------------------------
# The agent end to end
# ---------------------------------------------------------------------------
def test_the_agent_executes_a_qualifying_plan(tmp_sandbox, monkeypatch):
    """The ALLOW case, through the real execution path.

    The plan is supplied rather than generated because no branch of the current
    generator produces one that qualifies — the only high-confidence branch
    hardcodes `risk="medium"`, and creating a customer genuinely is a
    medium-risk action. That is a property of the generator, not of the gate,
    and it is stated here rather than worked around by lowering a threshold.

    `invoke_action` really runs against the fake backend: this is the execution
    path, not a simulation of it.
    """
    monkeypatch.setattr(
        agent_module, "execute_intent",
        lambda goal, app_id: {"parsed": {"intent": "search"}, "plan": qualifying_plan()},
    )
    result = ComputerAgent("contoso-crm").run("search customer")

    assert result["status"] == "completed", result
    assert result["gate"]["execute"] is True
    assert "did_not_execute_because" not in result
    assert len(result["executed"]) == 1
    assert result["executed"][0]["ok"] is True, result["executed"]


def test_the_agent_plans_and_names_why_it_did_not_act(tmp_sandbox):
    """The everyday case today: it plans, and says which condition stopped it."""
    result = ComputerAgent("contoso-crm").run("new customer")
    assert result["status"] == "planned"
    assert result["executed"] == []
    assert result["did_not_execute_because"], result
    assert result["gate"]["execute"] is False


def test_the_sandbox_still_refuses_when_the_gate_wrongly_says_execute(
    tmp_sandbox, monkeypatch
):
    """The test that matters most: the AI layer is not the defence.

    The gate is forced to say "execute" on a plan the policy denies. If the
    agent's own decision were the boundary, the action would go through. It
    does not, because `invoke_action` enforces the sandbox itself (#13) — so a
    wrong answer in the AI layer costs correctness of the report, never safety.
    """
    set_policy(SandboxPolicy(app_id="contoso-crm", denied_actions=["click_btn_search"]))
    monkeypatch.setattr(
        agent_module, "execute_intent",
        lambda goal, app_id: {"parsed": {"intent": "search"}, "plan": qualifying_plan()},
    )
    monkeypatch.setattr(agent_module, "evaluate", lambda plan, app_id: GateDecision(True))

    result = ComputerAgent("contoso-crm").run("search customer")

    # It tried — the gate said so — and the sandbox stopped it anyway.
    assert result["gate"]["execute"] is True
    assert len(result["executed"]) == 1
    assert result["executed"][0]["ok"] is False, result["executed"]
    assert result["executed"][0]["denied"] is True, result["executed"]


def test_the_agent_reports_the_gate_on_every_answer(tmp_sandbox):
    """`gate` is always present, so "did it consider executing?" is never a guess."""
    for goal in ("new customer", "save", "something with no match at all"):
        result = ComputerAgent("contoso-crm").run(goal)
        assert "gate" in result, result
        assert isinstance(result["gate"]["execute"], bool)


# ---------------------------------------------------------------------------
# The constants are part of the contract
# ---------------------------------------------------------------------------
def test_the_gate_constants_are_what_the_owner_specified():
    """Pinned: `confidence >= 0.8 AND risk == low`.

    Loosening either is a product decision, not a refactor, and this fails if
    one is changed without the decision being revisited.
    """
    assert MIN_CONFIDENCE == 0.8
    assert ALLOWED_RISK == "low"


def test_the_agent_no_longer_decides_on_requires_confirmation_alone():
    """Structural guard: the old one-condition branch must not come back.

    `if not plan.get("requires_confirmation")` was the whole guard, and it was
    true for every generated workflow — so the branch was dead while looking
    live. This fails if the gate is bypassed and that check is restored on its
    own.
    """
    import inspect

    source = inspect.getsource(ComputerAgent.run)
    assert "evaluate(" in source, "the agent no longer consults the gate"
    assert "requires_confirmation" not in source, (
        "the agent is deciding on requires_confirmation directly again; that "
        "belongs inside the gate, where every condition is named"
    )
