"""The execution gate: when may an agent act on its own plan?

Owner decision, issue #6 (2026-09-08): `ComputerAgent` keeps its execution path,
but the path is **declared** rather than reachable by accident.

    confidence >= 0.8  AND  risk == low  AND  security gate == ALLOW
                                ↓
                    invoke_action → sandbox → backend → audit

Any condition missing means `planned`, never a silent attempt.

What this module is NOT
-----------------------
It is not the security boundary. `invoke_action` enforces the sandbox itself
(#13), and that stays the enforcement point — a check the caller can skip is not
a check. This gate is consulted so the agent can *decide* whether to attempt and
*say why* it did not, which is a different job from stopping it.

Put another way: if this gate were wrong, `invoke_action` would still refuse.
There is a test that proves exactly that, because a defence that exists only in
the layer asking the question is not a defence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from windows_os_api.apps.sandbox.permissions import check_action

# A plan the agent may act on without asking. Deliberately strict on both axes:
# high confidence in a low-risk action. Either alone is not enough — being sure
# about something destructive is exactly the case that needs a human.
MIN_CONFIDENCE = 0.8
ALLOWED_RISK = "low"

# Why a plan was not executed. These are outcomes, not errors: "the agent
# planned and did not act" is a correct answer, and the caller should be able to
# tell which of the reasons applied.
REASON_CONFIDENCE = "CONFIDENCE_BELOW_THRESHOLD"
REASON_RISK = "RISK_NOT_LOW"
REASON_CONFIRMATION = "CONFIRMATION_REQUIRED"
REASON_POLICY = "DENIED_BY_POLICY"
REASON_NO_STEPS = "NO_STEPS_TO_EXECUTE"


@dataclass(frozen=True)
class GateDecision:
    """Whether to execute, and — when not — exactly which condition failed."""

    execute: bool
    reason: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"execute": self.execute}
        if self.reason:
            out["reason"] = self.reason
        if self.detail:
            out["detail"] = self.detail
        return out


def evaluate(plan: dict[str, Any], app_id: str) -> GateDecision:
    """Decide whether this plan may run unattended.

    Every condition is checked and named. Returning a bare False would leave the
    caller to guess whether the agent was unsure, the action was risky, or the
    policy said no — three situations with three different answers.
    """
    workflow = plan.get("workflow") or {}
    steps = workflow.get("steps") or []
    if not steps:
        return GateDecision(False, REASON_NO_STEPS, "the plan contains no steps")

    # The planner's own verdict comes first: it already folds in whatever the
    # planner knows, and overriding it here would make one of the two meaningless.
    if plan.get("requires_confirmation"):
        return GateDecision(
            False, REASON_CONFIRMATION,
            "the planner marked this plan as requiring confirmation",
        )

    confidence = plan.get("confidence")
    if not isinstance(confidence, (int, float)) or confidence < MIN_CONFIDENCE:
        return GateDecision(
            False, REASON_CONFIDENCE,
            f"confidence {confidence!r} is below the {MIN_CONFIDENCE} required to act unattended",
        )

    risk = plan.get("risk")
    if risk != ALLOWED_RISK:
        return GateDecision(
            False, REASON_RISK,
            f"risk {risk!r} is not {ALLOWED_RISK!r}; only low-risk plans run unattended",
        )

    # The sandbox has the last word, and it is asked about EVERY step: a plan is
    # only as safe as its most dangerous action, and stopping at the first
    # allowed one would let the rest through.
    for step in steps:
        action = step.get("action")
        verdict = check_action(app_id, action, risk)
        if not verdict.get("allowed"):
            return GateDecision(
                False, REASON_POLICY,
                f"sandbox policy refused {action!r}: {verdict.get('reason')}",
            )

    return GateDecision(True)
