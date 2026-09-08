"""Network APIs."""
from __future__ import annotations
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from windows_os_api.core.permissions.model import Permission
from windows_os_api.core.security.auth import AuthContext, require_permission
from windows_os_api.os.network import service as net

router = APIRouter(prefix="/network", tags=["network"])

@router.get("/interfaces")
def interfaces(auth: AuthContext = Depends(require_permission(Permission.NETWORK_READ))):
    return {"interfaces": net.interfaces()}

@router.get("/connections")
def connections(auth: AuthContext = Depends(require_permission(Permission.NETWORK_READ))):
    return {"connections": net.connections()}


class PingBody(BaseModel):
    host: str
    count: int = 2
    timeout: int = 2


class HostBody(BaseModel):
    host: str


class AddressBody(BaseModel):
    address: str


@router.get("/routes")
def routes(auth: AuthContext = Depends(require_permission(Permission.NETWORK_READ))):
    return {"routes": net.routes()}


# resolve/reverse/ping reach OUT to the network on a caller-supplied name, so
# they are POST rather than GET: they are not safe or idempotent in the HTTP
# sense, and they should not be cached or prefetched by anything in between.
@router.post("/dns/resolve")
def dns_resolve(body: HostBody,
                auth: AuthContext = Depends(require_permission(Permission.NETWORK_READ))):
    return net.dns_resolve(body.host)


@router.post("/dns/reverse")
def dns_reverse(body: AddressBody,
                auth: AuthContext = Depends(require_permission(Permission.NETWORK_READ))):
    return net.dns_reverse(body.address)


@router.post("/ping")
def ping(body: PingBody,
         auth: AuthContext = Depends(require_permission(Permission.NETWORK_READ))):
    return net.ping(body.host, body.count, body.timeout)
