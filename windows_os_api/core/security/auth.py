"""API key authentication and RBAC authorization (N011)."""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from windows_os_api.core.permissions.model import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    has_permission,
)
from windows_os_api.core.runtime.config import Settings, get_settings

# Documentation / suite placeholders — refused on the release path (N011 / H63-N011)
# unless Settings.allow_placeholder_api_keys is explicitly enabled (tests).
PLACEHOLDER_API_KEYS = frozenset(
    {
        "dev-key-change-me",
        "admin-key-change-me",
    }
)


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


def _match_key(provided: str, candidates: list[str]) -> bool:
    """Constant-time membership check against configured key material."""
    if not provided:
        return False
    found = False
    for candidate in candidates:
        if not candidate or len(provided) != len(candidate):
            continue
        # OR-accumulate so every equal-length candidate is compared.
        if secrets.compare_digest(provided, candidate):
            found = True
    return found


def _role_key_lists(settings: Settings) -> list[tuple[Role, list[str]]]:
    """All four roles are assignable via distinct key lists (N011)."""
    return [
        (Role.ADMIN, list(settings.admin_api_keys or [])),
        (Role.AUTOMATOR, list(settings.api_keys or [])),
        (Role.OPERATOR, list(settings.operator_api_keys or [])),
        (Role.VIEWER, list(settings.viewer_api_keys or [])),
    ]


def resolve_role(api_key: str, settings: Settings) -> Role:
    """Map an API key to exactly one Role.

    Multi-list hits are rejected (ambiguous identity). Unknown / placeholder
    (release path) keys are rejected as 401.
    """
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    if api_key in PLACEHOLDER_API_KEYS and not settings.allow_placeholder_api_keys:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    matched: list[Role] = []
    for role, keys in _role_key_lists(settings):
        if _match_key(api_key, keys):
            matched.append(role)
    if len(matched) != 1:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return matched[0]


# Back-compat alias used by older call sites / probes.
_resolve_role = resolve_role


def build_auth_context(api_key: str | None, settings: Settings) -> AuthContext:
    """Stable principal builder shared by REST, WS, and future MCP ingresses.

    When ``require_auth`` is False the principal is anonymous **VIEWER**
    (minimal scopes) — never anonymous ADMIN (N011 distributed-product rule).
    """
    if not settings.require_auth:
        perms = set(ROLE_PERMISSIONS.get(Role.VIEWER, set()))
        return AuthContext(
            api_key="anonymous",
            role=Role.VIEWER,
            subject="anonymous",
            permissions=perms,
        )
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )
    role = resolve_role(api_key, settings)
    perms = set(ROLE_PERMISSIONS.get(role, set()))
    redacted = (api_key[:8] + "...") if len(api_key) >= 8 else "***"
    subject = f"key:{api_key[:8]}" if len(api_key) >= 8 else "key:***"
    return AuthContext(api_key=redacted, role=role, subject=subject, permissions=perms)


def authenticate(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    ctx = build_auth_context(x_api_key, settings)
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
