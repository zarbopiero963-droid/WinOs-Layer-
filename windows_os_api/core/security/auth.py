"""API key authentication and RBAC authorization."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from windows_os_api.core.permissions.model import Permission, Role, has_permission
from windows_os_api.core.runtime.config import Settings, get_settings


@dataclass
class AuthContext:
    api_key: str
    role: Role
    subject: str
    permissions: set[Permission] = field(default_factory=set)

    def check(self, permission: Permission) -> bool:
        if Permission.ADMIN in self.permissions or self.role == Role.ADMIN:
            return True
        return permission in self.permissions or has_permission(self.role, permission)


def _resolve_role(api_key: str, settings: Settings) -> Role:
    if api_key in settings.admin_api_keys:
        return Role.ADMIN
    if api_key in settings.api_keys:
        return Role.AUTOMATOR
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def authenticate(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    if not settings.require_auth:
        return AuthContext(
            api_key="anonymous",
            role=Role.ADMIN,
            subject="anonymous",
            permissions=set(Permission),
        )
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )
    role = _resolve_role(x_api_key, settings)
    from windows_os_api.core.permissions.model import ROLE_PERMISSIONS

    perms = ROLE_PERMISSIONS.get(role, set())
    ctx = AuthContext(api_key=x_api_key[:8] + "...", role=role, subject=f"key:{x_api_key[:8]}", permissions=perms)
    request.state.auth = ctx
    return ctx


def require_permission(permission: Permission):
    def dependency(auth: AuthContext = Depends(authenticate)) -> AuthContext:
        if not auth.check(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {permission.value}",
            )
        return auth

    return dependency
