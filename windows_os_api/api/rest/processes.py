"""Process APIs."""
from __future__ import annotations
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission
from windows_os_api.api.rest.deps import audit
from windows_os_api.os.processes import service as procs

router = APIRouter(prefix="/processes", tags=["processes"])

# N047: rifiuti di policy/identita' => 403. `PROCESS_NOT_FOUND` resta nel corpo
# con `ok: False`, perche' «non c'e' piu'» non e' un divieto.
_IDENTITY_REFUSALS = frozenset(
    {"PROCESS_IDENTITY_MISMATCH", "PROCESS_IDENTITY_REQUIRED", "PROCESS_PROTECTED"}
)

class StartProcess(BaseModel):
    command: str
    args: list[str] = []

@router.get("")
def list_processes(auth: AuthContext = Depends(require_permission(Permission.PROCESS_READ))):
    return procs.list_processes()

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
    # N047: un avvio rifiutato dalla policy e' una richiesta non permessa, non
    # un successo con `ok: False` sepolto nel corpo. L'audit lo registra comunque.
    if not result.get("ok", True):
        raise HTTPException(403, {"detail": result.get("error"), "code": result.get("code")})
    return result

@router.delete("/{pid}")
def terminate(
    pid: int,
    expect_create_time: float | None = None,
    expect_name: str | None = None,
    auth: AuthContext = Depends(require_permission(Permission.PROCESS_EXECUTE)),
):
    """Termina un processo.

    N047: per un processo che questa API non ha avviato serve un'attesa
    esplicita (`expect_create_time` o `expect_name`) che ne confermi l'identita';
    il solo PID non basta, perche' il kernel lo ricicla.
    """
    result = procs.terminate_process(
        pid, expect_create_time=expect_create_time, expect_name=expect_name
    )
    audit("process.terminate", auth, resource=str(pid), detail=result)
    if not result.get("ok", True) and result.get("code") in _IDENTITY_REFUSALS:
        raise HTTPException(403, {"detail": result.get("error"), "code": result.get("code")})
    return result
