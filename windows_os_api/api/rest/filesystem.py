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
    try:
        return {"entries": fs.list_dir(path)}
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e

@router.get("/fs/read")
def read_file(path: str, auth: AuthContext = Depends(require_permission(Permission.FILESYSTEM_READ))):
    try:
        return fs.read_file(path)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e

@router.put("/fs")
def write_file(body: WriteBody, auth: AuthContext = Depends(require_permission(Permission.FILESYSTEM_WRITE))):
    try:
        result = fs.write_file(body.path, body.content)
        audit("fs.write", auth, resource=body.path)
        return result
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e

@router.delete("/fs")
def delete_file(path: str, auth: AuthContext = Depends(require_permission(Permission.FILESYSTEM_WRITE))):
    try:
        result = fs.delete_file(path)
        audit("fs.delete", auth, resource=path)
        return result
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e

@router.get("/storage/drives")
def drives(auth: AuthContext = Depends(require_permission(Permission.SYSTEM_READ))):
    return {"drives": storage.list_drives()}
