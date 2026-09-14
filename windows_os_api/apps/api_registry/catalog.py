"""N016 — API catalog query helpers (list / filter / search / pagination).

Pagination uses ``limit`` + ``offset`` (not page/page_size): stable across
inserts when callers sort by deterministic ``id``, and matches existing REST
``limit`` usage (e.g. audit log). Pages never emit duplicate ids when the
underlying filtered set is stable.

Authorized visibility is applied by the caller (REST) via ``visible_app_ids``:
pass ``None`` for ADMIN / unrestricted principals; pass a frozenset of app ids
to hide records outside that allowlist. Filtering by an ``application_id``
outside the allowlist is a policy error (``CatalogScopeDenied``) — 403 at REST.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from windows_os_api.apps.api_registry.model import ApiRecord, ApiRegistry, get_api_registry


class RegistryUnavailable(RuntimeError):
    """Raised when the authoritative registry cannot be consulted."""


class CatalogScopeDenied(PermissionError):
    """Caller asked for an application_id outside their app_scopes."""


DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@dataclass(frozen=True)
class CatalogPage:
    """One page of catalog results."""

    items: list[ApiRecord]
    total: int
    limit: int
    offset: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "apis": [r.to_dict() for r in self.items],
            "total": self.total,
            "limit": self.limit,
            "offset": self.offset,
        }


def _normalize_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    try:
        n = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if n < 1:
        raise ValueError("limit must be >= 1")
    return min(n, MAX_LIMIT)


def _normalize_offset(offset: int | None) -> int:
    if offset is None:
        return 0
    try:
        n = int(offset)
    except (TypeError, ValueError) as exc:
        raise ValueError("offset must be an integer") from exc
    if n < 0:
        raise ValueError("offset must be >= 0")
    return n


def resolve_registry(registry: ApiRegistry | None = None) -> ApiRegistry:
    """Return the registry or raise ``RegistryUnavailable``."""
    if registry is not None:
        return registry
    try:
        return get_api_registry()
    except Exception as exc:  # noqa: BLE001 — surface as unavailable
        raise RegistryUnavailable(str(exc) or "api registry unavailable") from exc


def _record_visible(record: ApiRecord, visible_app_ids: frozenset[str] | None) -> bool:
    if visible_app_ids is None:
        return True
    app = (record.application_id or "").strip()
    # Records without an application_id are system/native — visible only when
    # unrestricted (None). Scoped callers do not see empty-app records.
    if not app:
        return False
    return app in visible_app_ids


def _matches_search(record: ApiRecord, q: str) -> bool:
    needle = q.strip().lower()
    if not needle:
        return True
    haystacks = (
        record.id,
        record.name,
        record.path,
        record.capability,
        record.description,
        record.application_id,
        record.source,
    )
    return any(needle in (h or "").lower() for h in haystacks)


def _matches_permission(record: ApiRecord, permission: str | None, permissions: Sequence[str] | None) -> bool:
    wanted: list[str] = []
    if permission:
        wanted.append(str(permission).strip())
    if permissions:
        wanted.extend(str(p).strip() for p in permissions if str(p).strip())
    wanted = [w for w in wanted if w]
    if not wanted:
        return True
    have = {p.lower() for p in record.permissions}
    # Any listed permission must be present on the record (OR across values).
    return any(w.lower() in have for w in wanted)


def filter_records(
    records: Iterable[ApiRecord],
    *,
    q: str | None = None,
    source: str | None = None,
    application_id: str | None = None,
    status: str | None = None,
    permission: str | None = None,
    permissions: Sequence[str] | None = None,
    visible_app_ids: frozenset[str] | None = None,
) -> list[ApiRecord]:
    """Apply search/filters + visibility. Raises ``CatalogScopeDenied`` on bad app filter."""
    app_filter = (application_id or "").strip() or None
    if app_filter is not None and visible_app_ids is not None and app_filter not in visible_app_ids:
        raise CatalogScopeDenied(f"App scope denied: {app_filter}")

    src = (source or "").strip().lower() or None
    st = (status or "").strip().upper() or None
    query = (q or "").strip() or None

    out: list[ApiRecord] = []
    for rec in records:
        if not _record_visible(rec, visible_app_ids):
            continue
        if app_filter is not None and (rec.application_id or "").strip() != app_filter:
            continue
        if src is not None and (rec.source or "").strip().lower() != src:
            continue
        if st is not None and rec.status.value != st:
            continue
        if query is not None and not _matches_search(rec, query):
            continue
        if not _matches_permission(rec, permission, permissions):
            continue
        out.append(rec)
    # Deterministic order by id (ApiRegistry.list already sorts; re-sort for safety)
    out.sort(key=lambda r: r.id)
    return out


def list_catalog(
    *,
    registry: ApiRegistry | None = None,
    q: str | None = None,
    source: str | None = None,
    application_id: str | None = None,
    status: str | None = None,
    permission: str | None = None,
    permissions: Sequence[str] | None = None,
    visible_app_ids: frozenset[str] | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> CatalogPage:
    """List/filter/paginate registry records for the catalog REST surface."""
    reg = resolve_registry(registry)
    lim = _normalize_limit(limit)
    off = _normalize_offset(offset)
    filtered = filter_records(
        reg.list(),
        q=q,
        source=source,
        application_id=application_id,
        status=status,
        permission=permission,
        permissions=permissions,
        visible_app_ids=visible_app_ids,
    )
    total = len(filtered)
    page = filtered[off : off + lim]
    return CatalogPage(items=page, total=total, limit=lim, offset=off)


def get_catalog_record(
    api_id: str,
    *,
    registry: ApiRegistry | None = None,
    visible_app_ids: frozenset[str] | None = None,
) -> ApiRecord | None:
    """Return one record if present and visible; ``None`` if missing/hidden.

    Distinguishes missing (caller maps to 404) from unavailable
    (``RegistryUnavailable`` → 503). Hidden-by-scope is treated as missing
    (404) so callers cannot probe existence across app boundaries.
    """
    reg = resolve_registry(registry)
    rec = reg.get(api_id)
    if rec is None:
        return None
    if not _record_visible(rec, visible_app_ids):
        return None
    return rec
