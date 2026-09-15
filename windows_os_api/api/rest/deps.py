"""Shared FastAPI dependencies."""
from __future__ import annotations
from fastapi import Depends, HTTPException, Request, status
from windows_os_api.core.runtime.config import Settings, get_settings
from windows_os_api.core.security.auth import AuthContext, authenticate
from windows_os_api.core.security.rate_limit import RateLimiter, ConcurrencyGate
from windows_os_api.core.security.audit import get_audit_logger

_limiter: RateLimiter | None = None
_gate: ConcurrencyGate | None = None

def get_limiter(settings: Settings = Depends(get_settings)) -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(settings.rate_limit_per_minute)
    return _limiter


def get_concurrency_gate(settings: Settings = Depends(get_settings)) -> ConcurrencyGate:
    global _gate
    if _gate is None:
        # N046: il tetto globale resta quello di N013; la quota per principal gli
        # sta sotto e impedisce che un solo chiamante lo esaurisca da solo.
        _gate = ConcurrencyGate(
            settings.max_concurrent_requests,
            per_principal_limit=settings.max_concurrent_per_principal,
        )
    return _gate


def reset_limiter() -> None:
    """Drop the process-wide rate limiter so hit windows do not leak across tests."""
    global _limiter, _gate
    _limiter = None
    _gate = None


def rate_limited(
    request: Request,
    auth: AuthContext = Depends(authenticate),
    limiter: RateLimiter = Depends(get_limiter),
) -> AuthContext:
    key = auth.subject
    if not limiter.allow(key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
    return auth

def audit(
    action: str,
    auth: AuthContext,
    resource: str = "",
    detail=None,
    outcome="success",
    request_id: str | None = None,
    execution_id: str | None = None,
):
    """Persist a redacted, integrity-chained audit entry (N042).

    Propagates ``AuditUnavailableError`` so callers fail closed rather than
    silently dropping the audit record.
    """
    return get_audit_logger().log(
        action,
        subject=auth.subject,
        resource=resource,
        outcome=outcome,
        detail=detail or {},
        request_id=request_id,
        execution_id=execution_id,
    )
