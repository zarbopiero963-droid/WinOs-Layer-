"""N017 — Shared execution gateway (H63-N017).

Single authorize/execute path for REST today and MCP/GUI later. Never creates
adapters or API records on invoke. Registry status (DISABLED / ERROR / revoked /
stale VERIFIED / RESTRICTED / UNSUPPORTED) blocks execution as a **security**
decision; missing adapter and engine failures are **operational**.

Does **not** auto-assign permissions or sandbox policy.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from windows_os_api.apps.adapters.engine import get_adapter, invoke_action
from windows_os_api.apps.api_registry.model import (
    DEFAULT_VERIFICATION_MAX_AGE_SEC,
    ApiRecord,
    ApiRegistry,
    ApiStatus,
    authorize_verified_status,
    get_api_registry,
)

# Block kinds — security vs operational must stay distinct for callers/tests.
BLOCK_SECURITY = "security"
BLOCK_OPERATIONAL = "operational"


class ExecutionCode(str, Enum):
    """Stable deny / outcome codes for the gateway."""

    OK = "OK"
    API_NOT_FOUND = "API_NOT_FOUND"
    API_DISABLED = "API_DISABLED"
    API_REVOKED = "API_REVOKED"
    API_ERROR_STATUS = "API_ERROR_STATUS"
    API_RESTRICTED = "API_RESTRICTED"
    API_UNSUPPORTED = "API_UNSUPPORTED"
    API_STALE = "API_STALE"
    ADAPTER_ABSENT = "ADAPTER_ABSENT"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    APP_REQUIRED = "APP_REQUIRED"
    ENGINE_DENIED = "ENGINE_DENIED"
    ENGINE_ERROR = "ENGINE_ERROR"
    PRINCIPAL_REQUIRED = "PRINCIPAL_REQUIRED"
    PRINCIPAL_REVOKED = "PRINCIPAL_REVOKED"


@dataclass(frozen=True)
class AuthorizationDecision:
    """Result of ``authorize_execution`` (no side effects)."""

    allowed: bool
    code: str
    message: str
    block_kind: str | None  # "security" | "operational" | None when allowed
    record: ApiRecord | None = None
    app_id: str | None = None
    action_name: str | None = None
    effective_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "ok": self.allowed,
            "denied": (not self.allowed) and self.block_kind == BLOCK_SECURITY,
            "block_kind": self.block_kind,
            "code": self.code,
            "error": None if self.allowed else self.message,
            "message": self.message,
            "api_id": self.record.id if self.record else None,
            "app_id": self.app_id,
            "action": self.action_name,
            "effective_status": self.effective_status,
        }


def _deny(
    code: ExecutionCode | str,
    message: str,
    *,
    block_kind: str,
    record: ApiRecord | None = None,
    app_id: str | None = None,
    action_name: str | None = None,
    effective_status: str | None = None,
) -> AuthorizationDecision:
    code_s = code.value if isinstance(code, ExecutionCode) else str(code)
    return AuthorizationDecision(
        allowed=False,
        code=code_s,
        message=message,
        block_kind=block_kind,
        record=record,
        app_id=app_id,
        action_name=action_name,
        effective_status=effective_status,
    )


def _allow(
    *,
    record: ApiRecord | None,
    app_id: str,
    action_name: str,
    effective_status: str | None,
) -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=True,
        code=ExecutionCode.OK.value,
        message="authorized",
        block_kind=None,
        record=record,
        app_id=app_id,
        action_name=action_name,
        effective_status=effective_status,
    )


def find_api_for_action(
    registry: ApiRegistry,
    *,
    app_id: str,
    action_name: str,
) -> ApiRecord | None:
    """Locate a registry record for ``(app_id, action)`` when one exists.

    Matches projection shape from N015: capability ``{app_id}.{action}`` and
    path ``/v1/apps/{app_id}/actions/{action}``. Also accepts capability == action
    when application_id matches.
    """
    app_n = (app_id or "").strip()
    action_n = (action_name or "").strip()
    if not app_n or not action_n:
        return None
    expected_cap = f"{app_n}.{action_n}"
    expected_path_suffix = f"/apps/{app_n}/actions/{action_n}"
    matches: list[ApiRecord] = []
    for rec in registry.list():
        if (rec.application_id or "").strip() != app_n:
            continue
        cap = (rec.capability or "").strip()
        path = (rec.path or "").strip()
        if cap == expected_cap or cap == action_n:
            matches.append(rec)
            continue
        if path.endswith(expected_path_suffix) or path.endswith(f"/actions/{action_n}"):
            matches.append(rec)
    if not matches:
        return None
    # Prefer the most restrictive status if duplicates exist
    rank = {
        ApiStatus.DISABLED: 0,
        ApiStatus.ERROR: 1,
        ApiStatus.RESTRICTED: 2,
        ApiStatus.UNSUPPORTED: 3,
        ApiStatus.PARTIAL: 4,
        ApiStatus.VERIFIED: 5,
    }
    matches.sort(key=lambda r: (rank.get(r.status, 9), r.id))
    return matches[0]


def _status_blocks_execution(
    record: ApiRecord,
    *,
    now: float,
    max_age_sec: float,
) -> AuthorizationDecision | None:
    """Return a deny decision if registry status forbids execute; else None."""
    status = record.status
    # Stale VERIFIED: refuse execute (do not silently run as if still verified).
    if status is ApiStatus.VERIFIED:
        gated = authorize_verified_status(
            status=status,
            verification_id=record.verification_id,
            last_verified_at=record.last_verified_at,
            now=now,
            max_age_sec=max_age_sec,
        )
        if gated is not ApiStatus.VERIFIED:
            return _deny(
                ExecutionCode.API_STALE,
                "API verification is stale or incomplete; execution refused",
                block_kind=BLOCK_SECURITY,
                record=record,
                app_id=record.application_id or None,
                effective_status=gated.value,
            )

    if status is ApiStatus.DISABLED:
        # REVOKED maps to DISABLED at registration; treat as revoked/disabled.
        return _deny(
            ExecutionCode.API_DISABLED,
            "API is DISABLED; execution refused",
            block_kind=BLOCK_SECURITY,
            record=record,
            app_id=record.application_id or None,
            effective_status=status.value,
        )
    if status is ApiStatus.ERROR:
        return _deny(
            ExecutionCode.API_ERROR_STATUS,
            "API is in ERROR status; execution refused",
            block_kind=BLOCK_SECURITY,
            record=record,
            app_id=record.application_id or None,
            effective_status=status.value,
        )
    if status is ApiStatus.RESTRICTED:
        return _deny(
            ExecutionCode.API_RESTRICTED,
            "API is RESTRICTED; execution refused",
            block_kind=BLOCK_SECURITY,
            record=record,
            app_id=record.application_id or None,
            effective_status=status.value,
        )
    if status is ApiStatus.UNSUPPORTED:
        return _deny(
            ExecutionCode.API_UNSUPPORTED,
            "API is UNSUPPORTED; execution refused",
            block_kind=BLOCK_SECURITY,
            record=record,
            app_id=record.application_id or None,
            effective_status=status.value,
        )
    # PARTIAL and fresh VERIFIED are executable (PARTIAL ≠ verified success claim).
    return None


def authorize_execution(
    *,
    api_id: str | None = None,
    app_id: str | None = None,
    action_name: str | None = None,
    registry: ApiRegistry | None = None,
    now: float | None = None,
    require_registry_record: bool = False,
    adapter_lookup: Any | None = None,
    auth: Any | None = None,
    require_principal: bool = False,
) -> AuthorizationDecision:
    """Decide whether an invocation may proceed.

    Parameters
    ----------
    api_id:
        Lookup by registry id. Missing record → ``API_NOT_FOUND`` (operational).
    app_id / action_name:
        Adapter invoke coordinates. When a matching registry record exists,
        its status is enforced. When absent and ``require_registry_record`` is
        False, authorization continues (legacy adapters without catalog rows).
    require_registry_record:
        If True, missing API always denies (use for ``/v1/apis/{id}/execute``).
    adapter_lookup:
        Optional ``callable(app_id) -> adapter|None`` (tests). Default: ``get_adapter``.
    """
    clock = time.time() if now is None else now
    # N017: when a principal is supplied, re-check revoke; optional hard require.
    if auth is not None or require_principal:
        from fastapi import HTTPException
        from windows_os_api.core.security.auth import assert_active

        if auth is None:
            return _deny(
                ExecutionCode.PRINCIPAL_REQUIRED,
                "execution gateway requires an authenticated principal",
                block_kind=BLOCK_SECURITY,
            )
        try:
            assert_active(auth)
        except HTTPException:
            return _deny(
                ExecutionCode.PRINCIPAL_REVOKED,
                "principal revoked or session inactive",
                block_kind=BLOCK_SECURITY,
            )

    reg = registry if registry is not None else get_api_registry()
    max_age = getattr(reg, "_verification_max_age_sec", DEFAULT_VERIFICATION_MAX_AGE_SEC)
    lookup = adapter_lookup if adapter_lookup is not None else get_adapter

    record: ApiRecord | None = None
    app = (app_id or "").strip() or None
    action = (action_name or "").strip() or None

    if api_id is not None and str(api_id).strip():
        record = reg.get(str(api_id).strip())
        if record is None:
            return _deny(
                ExecutionCode.API_NOT_FOUND,
                f"API not found: {api_id}",
                block_kind=BLOCK_OPERATIONAL,
            )
        app = app or (record.application_id.strip() or None)
        # Derive action from capability ``app.action`` or path suffix when needed
        if action is None and record.capability:
            cap = record.capability
            if app and cap.startswith(f"{app}."):
                action = cap[len(app) + 1 :] or None
            elif "." in cap:
                action = cap.rsplit(".", 1)[-1]
            else:
                action = cap
        if action is None and record.path and "/actions/" in record.path:
            action = record.path.rstrip("/").rsplit("/actions/", 1)[-1] or None

    if record is None and app and action:
        record = find_api_for_action(reg, app_id=app, action_name=action)

    if record is None and require_registry_record:
        return _deny(
            ExecutionCode.API_NOT_FOUND,
            "API not found in registry",
            block_kind=BLOCK_OPERATIONAL,
            app_id=app,
            action_name=action,
        )

    if record is not None:
        blocked = _status_blocks_execution(record, now=clock, max_age_sec=max_age)
        if blocked is not None:
            return AuthorizationDecision(
                allowed=False,
                code=blocked.code,
                message=blocked.message,
                block_kind=blocked.block_kind,
                record=record,
                app_id=app or blocked.app_id,
                action_name=action,
                effective_status=blocked.effective_status,
            )
        # Explicit revoke via DISABLED already handled; lifecycle REVOKED→DISABLED
        # at register time. No separate revoke flag on ApiRecord today.

    if not app:
        return _deny(
            ExecutionCode.APP_REQUIRED,
            "app_id is required for execution",
            block_kind=BLOCK_OPERATIONAL,
            record=record,
            action_name=action,
        )
    if not action:
        return _deny(
            ExecutionCode.ACTION_REQUIRED,
            "action_name is required for execution",
            block_kind=BLOCK_OPERATIONAL,
            record=record,
            app_id=app,
        )

    adapter = lookup(app)
    if adapter is None:
        return _deny(
            ExecutionCode.ADAPTER_ABSENT,
            f"adapter not found for app_id={app!r}; create explicitly before invoke",
            block_kind=BLOCK_OPERATIONAL,
            record=record,
            app_id=app,
            action_name=action,
            effective_status=record.status.value if record else None,
        )

    effective = record.status.value if record else None
    if record and record.status is ApiStatus.VERIFIED:
        effective = ApiStatus.VERIFIED.value
    return _allow(
        record=record,
        app_id=app,
        action_name=action,
        effective_status=effective,
    )


def execute_via_gateway(
    *,
    api_id: str | None = None,
    app_id: str | None = None,
    action_name: str | None = None,
    params: Mapping[str, Any] | None = None,
    registry: ApiRegistry | None = None,
    now: float | None = None,
    require_registry_record: bool = False,
    adapter_lookup: Any | None = None,
    invoke: Any | None = None,
    auth: Any | None = None,
    require_principal: bool = False,
) -> dict[str, Any]:
    """Authorize then invoke. Never calls ``create_adapter``.

    Returns a dict always including ``ok``, ``block_kind`` (when denied),
    ``code``, and either engine result fields or deny fields. Security denials
    set ``denied=True``; operational failures set ``denied=False``.
    """
    decision = authorize_execution(
        api_id=api_id,
        app_id=app_id,
        action_name=action_name,
        registry=registry,
        now=now,
        require_registry_record=require_registry_record,
        adapter_lookup=adapter_lookup,
        auth=auth,
        require_principal=require_principal,
    )
    base = decision.to_dict()
    if not decision.allowed:
        return {
            **base,
            "ok": False,
            "created_adapter": False,
        }

    invoke_fn = invoke if invoke is not None else invoke_action
    assert decision.app_id and decision.action_name
    result = invoke_fn(decision.app_id, decision.action_name, dict(params or {}))
    if not isinstance(result, dict):
        result = {"ok": False, "error": "invalid engine result", "raw": result}

    # Engine sandbox deny → still a security block (policy), distinct from registry.
    if result.get("denied") is True or (
        result.get("ok") is False and "denied" in str(result.get("error", "")).lower()
        and result.get("denied") is not False
    ):
        # Prefer explicit denied flag from invoke_action
        if result.get("denied") is True:
            return {
                "ok": False,
                "denied": True,
                "block_kind": BLOCK_SECURITY,
                "code": ExecutionCode.ENGINE_DENIED.value,
                "error": result.get("error") or "policy denied",
                "message": result.get("error") or "policy denied",
                "api_id": decision.record.id if decision.record else None,
                "app_id": decision.app_id,
                "action": decision.action_name,
                "created_adapter": False,
                "engine": result,
            }

    if result.get("ok") is False:
        return {
            "ok": False,
            "denied": False,
            "block_kind": BLOCK_OPERATIONAL,
            "code": ExecutionCode.ENGINE_ERROR.value,
            "error": result.get("error") or "engine error",
            "message": result.get("error") or "engine error",
            "api_id": decision.record.id if decision.record else None,
            "app_id": decision.app_id,
            "action": decision.action_name,
            "created_adapter": False,
            "engine": result,
        }

    return {
        "ok": True,
        "denied": False,
        "block_kind": None,
        "code": ExecutionCode.OK.value,
        "error": None,
        "message": "executed",
        "api_id": decision.record.id if decision.record else None,
        "app_id": decision.app_id,
        "action": decision.action_name,
        "effective_status": decision.effective_status,
        "created_adapter": False,
        "result": result,
        # Flatten common engine fields for REST compatibility with prior invoke
        **{k: v for k, v in result.items() if k not in {"ok"}},
    }


def gateway_http_status(outcome: Mapping[str, Any]) -> int:
    """Map a gateway outcome to an HTTP status for REST wiring."""
    if outcome.get("ok"):
        return 200
    kind = outcome.get("block_kind")
    code = outcome.get("code")
    if kind == BLOCK_SECURITY:
        return 403
    if code in {
        ExecutionCode.API_NOT_FOUND.value,
        ExecutionCode.ADAPTER_ABSENT.value,
    }:
        return 404
    if code in {
        ExecutionCode.APP_REQUIRED.value,
        ExecutionCode.ACTION_REQUIRED.value,
    }:
        return 422
    return 409
