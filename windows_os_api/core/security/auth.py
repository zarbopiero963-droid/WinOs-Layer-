"""API key authentication and RBAC authorization (N011 + N012)."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
import uuid
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

# Known documentation placeholders. Release Settings defaults are empty (N011);
# installed-product preflight (N003) still forbids these as session keys.
PLACEHOLDER_API_KEYS = frozenset(
    {
        "dev-key-change-me",
        "admin-key-change-me",
    }
)


def _fingerprint(api_key: str) -> str:
    """Stable non-reversible id for a raw API key (revocation / session bind)."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


@dataclass
class AuthContext:
    api_key: str
    role: Role
    subject: str
    permissions: set[Permission] = field(default_factory=set)
    # N012 isolation / revocation surface
    key_fingerprint: str = ""
    session_id: str = ""
    user_id: str = ""
    app_scopes: frozenset[str] = field(default_factory=frozenset)

    def check(self, permission: Permission) -> bool:
        if Permission.ADMIN in self.permissions or self.role == Role.ADMIN:
            return True
        return permission in self.permissions or has_permission(self.role, permission)


@dataclass
class _SessionRecord:
    session_id: str
    key_fingerprint: str
    subject: str
    role: Role
    created_at: float
    revoked: bool = False


class AuthRegistry:
    """Runtime revocation, sessions, and app-scope bindings (N012).

    Server-side only. Clearing a key from Settings still 401s (N011); this
    registry revokes while the key remains configured — required for
    in-flight / reconnect deny.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._revoked_fps: set[str] = set()
        self._sessions: dict[str, _SessionRecord] = {}
        self._app_scopes: dict[str, frozenset[str]] = {}  # fp -> apps
        self._user_ids: dict[str, str] = {}  # fp -> user_id
        # Rotated-in keys not yet in Settings lists (fp -> Role)
        self._overlay_roles: dict[str, Role] = {}

    def reset(self) -> None:
        with self._lock:
            self._revoked_fps.clear()
            self._sessions.clear()
            self._app_scopes.clear()
            self._user_ids.clear()
            self._overlay_roles.clear()

    def is_revoked(self, api_key: str) -> bool:
        return self.is_revoked_fp(_fingerprint(api_key))

    def is_revoked_fp(self, fp: str) -> bool:
        with self._lock:
            return fp in self._revoked_fps

    def revoke_key(self, api_key: str) -> dict:
        fp = _fingerprint(api_key)
        with self._lock:
            self._revoked_fps.add(fp)
            revoked_sessions = 0
            for sess in self._sessions.values():
                if sess.key_fingerprint == fp and not sess.revoked:
                    sess.revoked = True
                    revoked_sessions += 1
            self._overlay_roles.pop(fp, None)
        return {"revoked": True, "key_fingerprint": fp[:16], "sessions_revoked": revoked_sessions}

    def revoke_session(self, session_id: str) -> dict:
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                raise HTTPException(status_code=404, detail="Unknown session")
            sess.revoked = True
            return {"revoked": True, "session_id": session_id}

    def session_key_fingerprint(self, session_id: str) -> str | None:
        with self._lock:
            sess = self._sessions.get(session_id)
            return None if sess is None else sess.key_fingerprint

    def is_session_revoked(self, session_id: str) -> bool:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                return True
            return sess.revoked or sess.key_fingerprint in self._revoked_fps

    def get_session(self, session_id: str) -> _SessionRecord | None:
        with self._lock:
            return self._sessions.get(session_id)

    def list_sessions_for_fingerprint(self, key_fingerprint: str) -> list[dict]:
        with self._lock:
            out = []
            for sess in self._sessions.values():
                if sess.key_fingerprint != key_fingerprint:
                    continue
                out.append({
                    "session_id": sess.session_id,
                    "subject": sess.subject,
                    "role": sess.role.value,
                    "created_at": sess.created_at,
                    "revoked": sess.revoked or sess.key_fingerprint in self._revoked_fps,
                })
            return out

    def issue_session(self, *, key_fingerprint: str, subject: str, role: Role) -> str:
        """Reuse an active session for this key fingerprint, else mint a new one."""
        with self._lock:
            for sess in self._sessions.values():
                if (
                    sess.key_fingerprint == key_fingerprint
                    and not sess.revoked
                    and sess.key_fingerprint not in self._revoked_fps
                ):
                    return sess.session_id
            sid = str(uuid.uuid4())
            self._sessions[sid] = _SessionRecord(
                session_id=sid,
                key_fingerprint=key_fingerprint,
                subject=subject,
                role=role,
                created_at=time.time(),
            )
            return sid

    def set_app_scopes(self, api_key: str, app_ids: list[str]) -> frozenset[str]:
        fp = _fingerprint(api_key)
        scopes = frozenset(a for a in app_ids if a)
        with self._lock:
            self._app_scopes[fp] = scopes
        return scopes

    def get_app_scopes(self, api_key: str) -> frozenset[str]:
        with self._lock:
            return self._app_scopes.get(_fingerprint(api_key), frozenset())

    def get_app_scopes_fp(self, fp: str) -> frozenset[str]:
        with self._lock:
            return self._app_scopes.get(fp, frozenset())

    def set_user_id(self, api_key: str, user_id: str) -> str:
        fp = _fingerprint(api_key)
        with self._lock:
            self._user_ids[fp] = user_id
        return user_id

    def get_user_id(self, api_key: str) -> str:
        with self._lock:
            return self._user_ids.get(_fingerprint(api_key), "")

    def register_overlay_key(self, api_key: str, role: Role) -> None:
        fp = _fingerprint(api_key)
        with self._lock:
            self._revoked_fps.discard(fp)
            self._overlay_roles[fp] = role

    def overlay_role(self, api_key: str) -> Role | None:
        with self._lock:
            return self._overlay_roles.get(_fingerprint(api_key))

    def rotate_key(self, old_key: str, new_key: str, role: Role) -> dict:
        if not new_key or new_key == old_key:
            raise HTTPException(status_code=400, detail="Invalid rotation target")
        if new_key in PLACEHOLDER_API_KEYS:
            raise HTTPException(status_code=400, detail="Placeholder keys cannot be rotation targets")
        # Revoke old first (side-effect boundary), then register new overlay.
        out = self.revoke_key(old_key)
        self.register_overlay_key(new_key, role)
        # Copy scopes/user from old fp to new if present
        old_fp = _fingerprint(old_key)
        new_fp = _fingerprint(new_key)
        with self._lock:
            if old_fp in self._app_scopes:
                self._app_scopes[new_fp] = self._app_scopes[old_fp]
            if old_fp in self._user_ids:
                self._user_ids[new_fp] = self._user_ids[old_fp]
        return {
            "rotated": True,
            "old_key_fingerprint": old_fp[:16],
            "new_key_fingerprint": new_fp[:16],
            "role": role.value,
            "sessions_revoked": out["sessions_revoked"],
        }


_REGISTRY = AuthRegistry()


def get_auth_registry() -> AuthRegistry:
    return _REGISTRY


def reset_auth_registry() -> None:
    _REGISTRY.reset()


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

    Multi-list hits are rejected (ambiguous identity). Keys absent from every
    configured list (including release defaults with empty lists) are 401.
    Revoked keys are 401 even if still present in Settings (N012).
    """
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    registry = get_auth_registry()
    if registry.is_revoked(api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key revoked")

    matched: list[Role] = []
    for role, keys in _role_key_lists(settings):
        if _match_key(api_key, keys):
            matched.append(role)
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    overlay = registry.overlay_role(api_key)
    if overlay is not None:
        return overlay

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


# Back-compat alias used by older call sites / probes.
_resolve_role = resolve_role


def key_in_settings(api_key: str, settings: Settings) -> bool:
    """True if api_key matches any configured Settings role list."""
    for _role, keys in _role_key_lists(settings):
        if _match_key(api_key, keys):
            return True
    return False


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
            key_fingerprint="",
            session_id="",
            user_id="anonymous",
            app_scopes=frozenset(),
        )
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )
    role = resolve_role(api_key, settings)
    perms = set(ROLE_PERMISSIONS.get(role, set()))
    # Display redaction only — never use a key prefix as the principal id.
    # Audit H63-N011: two distinct keys sharing the first 8 chars collided on
    # ``subject``, so ``ensure_resource_owner`` allowed cross-owner access.
    redacted = (api_key[:8] + "...") if len(api_key) >= 8 else "***"
    registry = get_auth_registry()
    fp = _fingerprint(api_key)
    subject = f"key:{fp}"
    user_id = registry.get_user_id(api_key) or subject
    scopes = registry.get_app_scopes(api_key)
    session_id = registry.issue_session(key_fingerprint=fp, subject=subject, role=role)
    return AuthContext(
        api_key=redacted,
        role=role,
        subject=subject,
        permissions=perms,
        key_fingerprint=fp,
        session_id=session_id,
        user_id=user_id,
        app_scopes=scopes,
    )


def assert_active(auth: AuthContext) -> None:
    """Re-check revocation at a side-effect boundary (N012).

    Ambiguous / revoked state is not success: raise 401.
    """
    if not auth.key_fingerprint and auth.subject == "anonymous":
        return
    registry = get_auth_registry()
    if auth.key_fingerprint and registry.is_revoked_fp(auth.key_fingerprint):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key revoked")
    if auth.session_id and registry.is_session_revoked(auth.session_id):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked")


def ensure_app_access(auth: AuthContext, app_id: str) -> None:
    """Deny cross-app use when the principal has explicit app_scopes (N012)."""
    assert_active(auth)
    if auth.role == Role.ADMIN:
        return
    registry = get_auth_registry()
    scopes = (
        registry.get_app_scopes_fp(auth.key_fingerprint)
        if auth.key_fingerprint
        else auth.app_scopes
    )
    if not scopes:
        return
    if app_id not in scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"App scope denied: {app_id}",
        )



def auth_context_from_mcp_params(
    params: dict | None,
    settings: Settings | None = None,
) -> AuthContext:
    """Build the same ``AuthContext`` REST/WS use, from MCP ``params._meta``.

    N011: MCP must not invent a parallel identity. Credentials come from
    ``params._meta.api_key`` / ``x-api-key`` (never from query strings).
    Tool-level gating / tools/list deny without credentials remains N020;
    this helper only constructs the shared principal when called.
    """
    settings = settings or get_settings()
    api_key: str | None = None
    if isinstance(params, dict):
        meta = params.get("_meta")
        if isinstance(meta, dict):
            raw = meta.get("api_key")
            if raw is None:
                raw = meta.get("x-api-key")
            if raw is not None:
                api_key = str(raw)
    return build_auth_context(api_key, settings)


def ensure_resource_owner(auth: AuthContext, owner_subject: str) -> None:
    """Deny cross-user resource access unless ADMIN (N012 isolation).

    Legacy resources with an empty owner_subject remain readable/playable
    (pre-N012); new recordings always bind owner_subject.
    """
    assert_active(auth)
    if auth.role == Role.ADMIN:
        return
    if not owner_subject:
        return
    if owner_subject != auth.subject:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Resource owned by another principal",
        )


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
        assert_active(auth)
        if not auth.check(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {permission.value}",
            )
        return auth

    return dependency
