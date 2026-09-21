"""Filesystem + storage APIs."""
from __future__ import annotations
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission
from windows_os_api.api.rest.deps import audit
from windows_os_api.os.filesystem import service as fs
from windows_os_api.os.storage import service as storage

router = APIRouter(tags=["filesystem"])

class WriteBody(BaseModel):
    path: str
    content: str

@router.get("/fs")
def list_dir(path: str = ".", auth: AuthContext = Depends(require_permission(Permission.FILESYSTEM_READ))):
    result = fs.list_dir(path)
    if isinstance(result, dict) and result.get("ok") is False:
        raise HTTPException(403, {"detail": result.get("error"), "code": result.get("code")})
    return {"entries": result}

@router.get("/fs/read")
def read_file(path: str, auth: AuthContext = Depends(require_permission(Permission.FILESYSTEM_READ))):
    result = fs.read_file(path)
    if not result.get("ok", True):
        status = 404 if result.get("code") == "PATH_NOT_FOUND" else 403
        raise HTTPException(status, {"detail": result.get("error"), "code": result.get("code")})
    return result

@router.put("/fs")
def write_file(body: WriteBody, auth: AuthContext = Depends(require_permission(Permission.FILESYSTEM_WRITE))):
    result = fs.write_file(body.path, body.content)
    audit("fs.write", auth, resource=body.path, detail=result)
    if not result.get("ok", True):
        raise HTTPException(403, {"detail": result.get("error"), "code": result.get("code")})
    return result

@router.delete("/fs")
def delete_file(path: str, auth: AuthContext = Depends(require_permission(Permission.FILESYSTEM_WRITE))):
    result = fs.delete_file(path)
    audit("fs.delete", auth, resource=path, detail=result)
    if not result.get("ok", True):
        raise HTTPException(403, {"detail": result.get("error"), "code": result.get("code")})
    return result



@router.get("/fs/stat")
def stat_file(path: str, auth: AuthContext = Depends(require_permission(Permission.FILESYSTEM_READ))):
    """N049 — metadati file sotto sandbox (lstat, no follow)."""
    result = fs.stat_file(path)
    if not result.get("ok", True):
        raise HTTPException(403, {"detail": result.get("error"), "code": result.get("code")})
    return result

@router.get("/fs/hash")
def hash_file(
    path: str,
    algo: str = "sha256",
    auth: AuthContext = Depends(require_permission(Permission.FILESYSTEM_READ)),
):
    """N049 — hash file sotto sandbox senza seguire symlink."""
    result = fs.hash_file(path, algo=algo)
    if not result.get("ok", True):
        raise HTTPException(403, {"detail": result.get("error"), "code": result.get("code")})
    return result

@router.get("/storage/drives")
def drives(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return storage.list_drives()
