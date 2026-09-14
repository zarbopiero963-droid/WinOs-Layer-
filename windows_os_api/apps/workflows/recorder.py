"""Workflow recorder / player."""
from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

@dataclass
class WorkflowStep:
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

@dataclass
class Workflow:
    id: str
    name: str
    steps: list[WorkflowStep] = field(default_factory=list)
    app_id: str = ""
    confidence: float = 1.0
    risk: str = "low"
    rollback: list[WorkflowStep] = field(default_factory=list)
    owner_subject: str = ""  # N012 — principal isolation

_workflows: dict[str, Workflow] = {}
_recording: Workflow | None = None

def start_recording(name: str, app_id: str = "", owner_subject: str = "") -> Workflow:
    global _recording
    wf = Workflow(id=str(uuid.uuid4()), name=name, app_id=app_id, owner_subject=owner_subject)
    _recording = wf
    _workflows[wf.id] = wf
    return wf

def record_step(action: str, params: dict[str, Any] | None = None) -> WorkflowStep:
    if _recording is None:
        raise RuntimeError("no active recording")
    step = WorkflowStep(action=action, params=params or {})
    _recording.steps.append(step)
    return step

def stop_recording() -> Workflow | None:
    global _recording
    wf = _recording
    _recording = None
    return wf

def get_workflow(wf_id: str) -> Workflow | None:
    return _workflows.get(wf_id)

def list_workflows(owner_subject: str | None = None, *, include_all: bool = False) -> list[dict[str, Any]]:
    items = []
    for w in _workflows.values():
        if not include_all and owner_subject is not None and w.owner_subject and w.owner_subject != owner_subject:
            continue
        items.append({
            "id": w.id, "name": w.name, "steps": len(w.steps), "app_id": w.app_id,
            "confidence": w.confidence, "risk": w.risk, "owner_subject": w.owner_subject,
        })
    return items


def _step_succeeded(result: Any) -> bool:
    """Fail-closed: only True or dict with ok is True (bool) and not denied/error."""
    if result is True:
        return True
    if isinstance(result, dict):
        if result.get("denied") or result.get("error"):
            return False
        return result.get("ok") is True
    return False


def play(
    wf_id: str,
    invoke,
    *,
    before_step: Callable[[int, WorkflowStep], None] | None = None,
) -> dict[str, Any]:
    """Esegue gli step; ok è True solo se tutti hanno successo.

    Al primo fallimento si interrompe (fail-closed) e, se il workflow ha
    `rollback`, prova quella sequenza. Non si continua a premere pulsanti
    dopo uno step già fallito.

    ``before_step`` is the N012 side-effect gate (e.g. assert_active): called
    immediately before each invoke. If it raises, the step is recorded as
    denied/failed and play stops.
    """
    wf = _workflows.get(wf_id)
    if not wf:
        return {"ok": False, "error": "workflow not found"}
    results: list[Any] = []
    failed_at: int | None = None
    for index, step in enumerate(wf.steps):
        if before_step is not None:
            try:
                before_step(index, step)
            except Exception as exc:  # noqa: BLE001 — gate deny is a failed step
                status = getattr(exc, "status_code", None)
                detail = getattr(exc, "detail", str(exc))
                result = {
                    "ok": False,
                    "denied": True,
                    "error": detail,
                    "action": step.action,
                    "status_code": status,
                }
                results.append(result)
                failed_at = index
                break
        try:
            result = invoke(wf.app_id, step.action, step.params)
        except Exception as exc:  # noqa: BLE001 — player must not crash the API
            result = {"ok": False, "error": str(exc), "action": step.action}
        results.append(result)
        if not _step_succeeded(result):
            failed_at = index
            break
    payload: dict[str, Any] = {
        "ok": failed_at is None,
        "workflow_id": wf_id,
        "results": results,
    }
    if failed_at is None:
        return payload
    payload["failed_step"] = failed_at
    payload["failed_action"] = wf.steps[failed_at].action
    if not wf.rollback:
        return payload
    recovery: list[Any] = []
    recovered = True
    for step in wf.rollback:
        if before_step is not None:
            try:
                before_step(-1, step)
            except Exception as exc:  # noqa: BLE001
                status = getattr(exc, "status_code", None)
                detail = getattr(exc, "detail", str(exc))
                recovered_result = {
                    "ok": False,
                    "denied": True,
                    "error": detail,
                    "action": step.action,
                    "status_code": status,
                }
                recovery.append(recovered_result)
                recovered = False
                break
        try:
            recovered_result = invoke(wf.app_id, step.action, step.params)
        except Exception as exc:  # noqa: BLE001
            recovered_result = {"ok": False, "error": str(exc), "action": step.action}
        recovery.append(recovered_result)
        if not _step_succeeded(recovered_result):
            recovered = False
            break
    payload["recovery"] = recovery
    payload["recovered"] = recovered
    return payload

def to_dict(wf: Workflow) -> dict[str, Any]:
    return {
        "id": wf.id, "name": wf.name, "app_id": wf.app_id,
        "confidence": wf.confidence, "risk": wf.risk,
        "owner_subject": wf.owner_subject,
        "steps": [asdict(s) for s in wf.steps],
        "rollback": [asdict(s) for s in wf.rollback],
    }

def reset_workflows() -> None:
    global _workflows, _recording
    _workflows = {}
    _recording = None
