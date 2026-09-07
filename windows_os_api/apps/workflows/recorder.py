"""Workflow recorder / player."""
from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

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

_workflows: dict[str, Workflow] = {}
_recording: Workflow | None = None

def start_recording(name: str, app_id: str = "") -> Workflow:
    global _recording
    wf = Workflow(id=str(uuid.uuid4()), name=name, app_id=app_id)
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

def list_workflows() -> list[dict[str, Any]]:
    return [{"id": w.id, "name": w.name, "steps": len(w.steps), "app_id": w.app_id,
             "confidence": w.confidence, "risk": w.risk} for w in _workflows.values()]

def play(wf_id: str, invoke) -> dict[str, Any]:
    wf = _workflows.get(wf_id)
    if not wf:
        return {"ok": False, "error": "workflow not found"}
    results = []
    for step in wf.steps:
        results.append(invoke(wf.app_id, step.action, step.params))
    return {"ok": True, "workflow_id": wf_id, "results": results}

def to_dict(wf: Workflow) -> dict[str, Any]:
    return {
        "id": wf.id, "name": wf.name, "app_id": wf.app_id,
        "confidence": wf.confidence, "risk": wf.risk,
        "steps": [asdict(s) for s in wf.steps],
        "rollback": [asdict(s) for s in wf.rollback],
    }

def reset_workflows() -> None:
    global _recording
    _workflows.clear()
    _recording = None
