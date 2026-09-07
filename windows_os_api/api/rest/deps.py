"""Shared FastAPI dependencies."""
from __future__ import annotations
from fastapi import Depends, HTTPException, Request, status
from windows_os_api.core.runtime.config import Settings, get_settings
from windows_os_api.core.security.auth import AuthContext, authenticate
from windows_os_api.core.security.rate_limit import RateLimiter
from windows_os_api.core.security.audit import get_audit_logger

_limiter: RateLimiter | None = None

def get_limiter(settings: Settings = Depends(get_settings)) -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(settings.rate_limit_per_minute)
    return _limiter

def rate_limited(
    request: Request,
    auth: AuthContext = Depends(authenticate),
    limiter: RateLimiter = Depends(get_limiter),
) -> AuthContext:
    key = auth.subject
    if not limiter.allow(key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
    return auth

def audit(action: str, auth: AuthContext, resource: str = "", detail=None, outcome="success"):
    get_audit_logger().log(action, subject=auth.subject, resource=resource, outcome=outcome, detail=detail or {})
