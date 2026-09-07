"""Process APIs."""
from __future__ import annotations
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission
from windows_os_api.api.rest.deps import audit
from windows_os_api.os.processes import service as procs

router = APIRouter(prefix="/processes", tags=["processes"])

class StartProcess(BaseModel):
    command: str
    args: list[str] = []

@router.get("")
def list_processes(auth: AuthContext = Depends(require_permission(Permission.PROCESS_READ))):
    return {"processes": procs.list_processes()}

@router.get("/{pid}")
def get_process(pid: int, auth: AuthContext = Depends(require_permission(Permission.PROCESS_READ))):
    p = procs.get_process(pid)
    if not p:
        raise HTTPException(404, "process not found")
    return p

@router.post("")
def start(body: StartProcess, auth: AuthContext = Depends(require_permission(Permission.PROCESS_EXECUTE))):
    result = procs.start_process(body.command, body.args)
    audit("process.start", auth, resource=body.command, detail=result)
    return result

@router.delete("/{pid}")
def terminate(pid: int, auth: AuthContext = Depends(require_permission(Permission.PROCESS_EXECUTE))):
    result = procs.terminate_process(pid)
    audit("process.terminate", auth, resource=str(pid), detail=result)
    return result
