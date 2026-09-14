"""N018 — API Test: execution linked to independent postcondition + verification id.

Contract (H63-N018 / issue #61 §7 + §20)
----------------------------------------
``POST /v1/apis/{api_id}/test`` runs the shared N017 gateway, then checks an
**independent** postcondition (UI readback for Edit; undefined for Button).

Rules:
* ``success`` is True only when execution ok **and** postcondition PASS **and**
  a ``verification_id`` was minted.
* HTTP 200 / gateway ``ok`` alone never imply success when state is unchanged.
* Insufficient proof → ``verification.status=PARTIAL``, ``verified=false``.
* Never mint registry VERIFIED without ``verification_id`` + ``last_verified_at``.
* Ambiguous state ≠ success.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Mapping

from windows_os_api.apps.adapters.engine import get_adapter
from windows_os_api.apps.api_registry.gateway import (
    BLOCK_OPERATIONAL,
    BLOCK_SECURITY,
    execute_via_gateway,
)
from windows_os_api.apps.api_registry.model import (
    ApiRecord,
    ApiRegistry,
    ApiStatus,
    get_api_registry,
)

# Verification statuses for the API Test envelope (distinct from ApiStatus).
V_PASS = "PASS"
V_PARTIAL = "PARTIAL"
V_FAIL = "FAIL"
V_BLOCKED = "BLOCKED"
V_UNSUPPORTED = "UNSUPPORTED"


def _mint_execution_id() -> str:
    return f"exec_{uuid.uuid4().hex}"


def _mint_verification_id() -> str:
    return f"ver_{uuid.uuid4().hex}"


def _node(tree: dict[str, Any], automation_id: str) -> dict[str, Any] | None:
    from windows_os_api.apps.ui_inspector.service import find_by_automation_id

    return find_by_automation_id(tree, automation_id)


def observe_edit_value(app_id: str, action_name: str) -> dict[str, Any]:
    """Independent readback of an Edit control value from the live UI tree.

    Does **not** trust invoke ``ok`` or any value the caller claimed to write.
    """
    adapter = get_adapter(app_id)
    if adapter is None:
        return {
            "ok": False,
            "code": "ADAPTER_ABSENT",
            "observed_value": None,
            "evidence": f"no adapter for {app_id!r}",
        }
    if not adapter.bound:
        return {
            "ok": False,
            "code": "ADAPTER_NOT_BOUND",
            "observed_value": None,
            "evidence": "adapter not bound to a live window",
        }
    action = next((a for a in adapter.actions if a.name == action_name), None)
    if action is None:
        return {
            "ok": False,
            "code": "ACTION_NOT_FOUND",
            "observed_value": None,
            "evidence": f"action {action_name!r} missing",
        }
    if action.control_type != "Edit":
        return {
            "ok": False,
            "code": "NOT_EDIT",
            "observed_value": None,
            "control_type": action.control_type,
            "evidence": f"control_type {action.control_type!r} has no Edit readback",
        }

    from windows_os_api.backends.factory import get_backend

    try:
        tree = get_backend().get_ui_tree(adapter.hwnd)
        node = _node(tree, action.automation_id)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "code": "OBSERVATION_FAILED",
            "observed_value": None,
            "error_type": type(exc).__name__,
            "evidence": "failed to read UI tree for postcondition",
        }
    if node is None:
        return {
            "ok": False,
            "code": "ELEMENT_NOT_FOUND",
            "observed_value": None,
            "evidence": "control not present in current UI tree",
        }
    if "value" not in node or node.get("value") is None:
        return {
            "ok": False,
            "code": "VALUE_UNAVAILABLE",
            "observed_value": None,
            "evidence": "backend does not expose a readable value for postcondition",
        }
    return {
        "ok": True,
        "code": "OBSERVED",
        "observed_value": str(node["value"]),
        "automation_id": action.automation_id,
        "evidence": "value read back from live UI tree",
    }


def _evaluate_postcondition(
    *,
    app_id: str,
    action_name: str,
    params: Mapping[str, Any],
    before: dict[str, Any] | None,
    execution_ok: bool,
) -> dict[str, Any]:
    """Compare independent observations to decide PASS / PARTIAL / FAIL.

    Edit + expected ``value``: PASS only if after readback equals expected and
    (when before was readable) the value actually changed or already matched
    only after a successful write path that we re-read.

    If invoke claimed ok but the observed value is unchanged from before while
    the requested value differs → FAIL (HTTP/ok alone is not success).
    """
    adapter = get_adapter(app_id)
    action = None
    if adapter is not None:
        action = next((a for a in adapter.actions if a.name == action_name), None)

    if action is None:
        return {
            "status": V_PARTIAL,
            "verified": False,
            "code": "POSTCONDITION_UNDEFINED",
            "evidence": "no adapter action to define an independent postcondition",
            "effects": [],
        }

    if action.control_type in ("Button", "MenuItem"):
        return {
            "status": V_UNSUPPORTED,
            "verified": False,
            "code": "EXPECTED_EFFECT_UNDEFINED",
            "evidence": (
                f"{action_name!r} does not declare an observable effect; "
                "API Test cannot claim VERIFIED"
            ),
            "effects": [],
        }

    if action.control_type != "Edit":
        return {
            "status": V_PARTIAL,
            "verified": False,
            "code": "POSTCONDITION_UNSUPPORTED",
            "evidence": (
                f"no independent postcondition for control_type "
                f"{action.control_type!r}"
            ),
            "effects": [],
        }

    if "value" not in params:
        return {
            "status": V_PARTIAL,
            "verified": False,
            "code": "EXPECTED_VALUE_MISSING",
            "evidence": "Edit API Test requires params.value as the expected postcondition",
            "effects": [],
        }

    expected = str(params.get("value"))
    after = observe_edit_value(app_id, action_name)
    effects = [
        {
            "kind": "ui_value",
            "automation_id": action.automation_id,
            "before": (before or {}).get("observed_value"),
            "after": after.get("observed_value"),
            "expected": expected,
        }
    ]

    if not execution_ok:
        return {
            "status": V_FAIL,
            "verified": False,
            "code": "EXECUTION_FAILED",
            "evidence": "execution did not succeed; postcondition not claimed",
            "effects": effects,
            "observation": after,
        }

    if not after.get("ok"):
        return {
            "status": V_PARTIAL,
            "verified": False,
            "code": after.get("code") or "OBSERVATION_INCOMPLETE",
            "evidence": after.get("evidence") or "insufficient evidence for PASS",
            "effects": effects,
            "observation": after,
        }

    observed = str(after["observed_value"])
    before_val = (before or {}).get("observed_value")
    before_ok = bool((before or {}).get("ok"))

    # Classic H63-N018: app accepted command (execution_ok) but state unchanged
    # while a different value was requested → NOT success.
    if (
        before_ok
        and before_val is not None
        and str(before_val) == observed
        and observed != expected
    ):
        return {
            "status": V_FAIL,
            "verified": False,
            "code": "EFFECT_NOT_OBSERVED",
            "evidence": (
                "execution reported ok but independent readback is unchanged "
                "and does not match the expected value; HTTP/ok is not success"
            ),
            "effects": effects,
            "observation": after,
        }

    if observed != expected:
        return {
            "status": V_FAIL,
            "verified": False,
            "code": "EFFECT_MISMATCH",
            "evidence": (
                f"independent readback {observed!r} != expected {expected!r}"
            ),
            "effects": effects,
            "observation": after,
        }

    return {
        "status": V_PASS,
        "verified": True,
        "code": "EFFECT_OBSERVED",
        "evidence": (
            "independent UI readback matches expected value after execution"
        ),
        "effects": effects,
        "observation": after,
    }


def _maybe_update_registry(
    registry: ApiRegistry,
    record: ApiRecord,
    *,
    verification_id: str,
    now: float,
) -> ApiRecord | None:
    """Register VERIFIED only with fresh verification_id (never fabricate)."""
    payload = record.to_dict()
    payload["status"] = ApiStatus.VERIFIED.value
    payload["verification_id"] = verification_id
    payload["last_verified_at"] = now
    try:
        return registry.register(payload)
    except Exception:  # noqa: BLE001 — registration rules may demote; surface in result
        return None


def run_api_test(
    api_id: str,
    *,
    params: Mapping[str, Any] | None = None,
    registry: ApiRegistry | None = None,
    update_registry_on_pass: bool = True,
    invoke: Any | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Run API Test: gateway execute + independent postcondition.

    Returns the #61-style envelope. ``success`` requires PASS + verification_id.
    """
    started = time.perf_counter()
    clock = time.time() if now is None else now
    execution_id = _mint_execution_id()
    reg = registry if registry is not None else get_api_registry()
    params_d = dict(params or {})

    record = reg.get(str(api_id).strip())
    if record is None:
        return {
            "success": False,
            "ok": False,
            "api_id": api_id,
            "execution_id": execution_id,
            "verification_id": None,
            "verification": {
                "status": V_FAIL,
                "verified": False,
                "code": "API_NOT_FOUND",
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "effects": [],
            "error": f"API not found: {api_id}",
            "http_ok_alone": False,
        }

    app_id = (record.application_id or "").strip()
    action_name = None
    cap = (record.capability or "").strip()
    if app_id and cap.startswith(f"{app_id}."):
        action_name = cap[len(app_id) + 1 :] or None
    elif cap:
        action_name = cap.rsplit(".", 1)[-1]
    if not action_name and record.path and "/actions/" in record.path:
        action_name = record.path.rstrip("/").rsplit("/actions/", 1)[-1] or None

    before = None
    if app_id and action_name:
        adapter = get_adapter(app_id)
        action = (
            next((a for a in adapter.actions if a.name == action_name), None)
            if adapter
            else None
        )
        if action is not None and action.control_type == "Edit":
            before = observe_edit_value(app_id, action_name)

    execution = execute_via_gateway(
        api_id=record.id,
        app_id=app_id or None,
        action_name=action_name,
        params=params_d,
        registry=reg,
        now=clock,
        require_registry_record=True,
        invoke=invoke,
    )
    execution_ok = bool(execution.get("ok"))

    if execution.get("block_kind") == BLOCK_SECURITY:
        post = {
            "status": V_BLOCKED,
            "verified": False,
            "code": execution.get("code") or "SECURITY_BLOCK",
            "evidence": execution.get("error") or execution.get("message") or "blocked",
            "effects": [],
        }
    elif app_id and action_name:
        post = _evaluate_postcondition(
            app_id=app_id,
            action_name=action_name,
            params=params_d,
            before=before,
            execution_ok=execution_ok,
        )
    else:
        post = {
            "status": V_PARTIAL,
            "verified": False,
            "code": "POSTCONDITION_UNDEFINED",
            "evidence": "cannot resolve app/action for independent postcondition",
            "effects": [],
        }

    verification_id = None
    registry_updated = False
    if post.get("verified") is True and post.get("status") == V_PASS:
        verification_id = _mint_verification_id()
        if update_registry_on_pass:
            updated = _maybe_update_registry(
                reg, record, verification_id=verification_id, now=clock
            )
            registry_updated = updated is not None and updated.status is ApiStatus.VERIFIED

    success = bool(
        execution_ok
        and post.get("verified") is True
        and post.get("status") == V_PASS
        and verification_id
    )

    duration_ms = int((time.perf_counter() - started) * 1000)
    error = None
    if not success:
        error = (
            post.get("evidence")
            or execution.get("error")
            or execution.get("message")
            or "API Test did not produce verified success"
        )

    return {
        "success": success,
        # Alias: callers must not treat ok as verified success (N018 / §20).
        "ok": success,
        "api_id": record.id,
        "execution_id": execution_id,
        "verification_id": verification_id,
        "verification": {
            "status": post.get("status"),
            "verified": bool(post.get("verified")),
            "code": post.get("code"),
            "evidence": post.get("evidence"),
            "state": post.get("status"),  # convenience alias
        },
        "duration_ms": duration_ms,
        "effects": list(post.get("effects") or []),
        "error": error if not success else None,
        "execution": {
            "ok": execution_ok,
            "code": execution.get("code"),
            "block_kind": execution.get("block_kind"),
            "denied": bool(execution.get("denied")),
            "message": execution.get("message") or execution.get("error"),
        },
        "http_ok_alone": False,
        "registry_updated": registry_updated,
        "observation": post.get("observation"),
        "app_id": app_id or None,
        "action": action_name,
    }


__all__ = [
    "V_BLOCKED",
    "V_FAIL",
    "V_PARTIAL",
    "V_PASS",
    "V_UNSUPPORTED",
    "observe_edit_value",
    "run_api_test",
]
