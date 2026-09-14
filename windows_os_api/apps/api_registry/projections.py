"""Project native / adapter / workflow APIs into registry payloads (N015).

Projections produce registration payloads for ``ApiRegistry.register``.
They **never** mint VERIFIED from adapter/workflow **manifest** fields alone.

Manifest / adapter verification blobs may map to PARTIAL / ERROR / RESTRICTED /
UNSUPPORTED / DISABLED, but VERIFIED requires independent registry evidence:
``verification_id`` + fresh ``last_verified_at`` passed explicitly (not from
the manifest). ``register()`` still re-runs ``authorize_verified_status``.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from windows_os_api.apps.api_registry.model import (
    ApiRegistry,
    ApiStatus,
    map_lifecycle_to_status,
)

# Curated native OS surface (prefix /v1). Discovery ≠ verification → PARTIAL.
_NATIVE_CATALOG: tuple[dict[str, str], ...] = (
    {"name": "Health", "method": "GET", "path": "/v1/health", "capability": "system.health"},
    {"name": "Ready", "method": "GET", "path": "/v1/ready", "capability": "system.ready"},
    {"name": "System info", "method": "GET", "path": "/v1/system", "capability": "system.info"},
    {"name": "Capabilities", "method": "GET", "path": "/v1/capabilities", "capability": "system.capabilities"},
    {"name": "List windows", "method": "GET", "path": "/v1/windows", "capability": "windows.list"},
    {"name": "List processes", "method": "GET", "path": "/v1/processes", "capability": "processes.list"},
    {"name": "Filesystem list", "method": "GET", "path": "/v1/fs", "capability": "fs.list"},
)


def _manifest_lifecycle_to_honest_status(lifecycle: str | None) -> ApiStatus:
    """Map adapter/workflow verification labels → non-VERIFIED #61 status.

    Even a manifest claiming VERIFIED / PASS becomes PARTIAL here: the
    projection must not treat the blob as proof of effect verification.
    """
    if lifecycle is None:
        return ApiStatus.PARTIAL
    key = str(lifecycle).strip().upper()
    if not key:
        return ApiStatus.PARTIAL
    # Never promote manifest VERIFIED/PASS to registry VERIFIED.
    if key in {"VERIFIED", "PASS", "GENERATED", "DISCOVERED", "UNKNOWN", "UNSTABLE"}:
        return ApiStatus.PARTIAL
    mapped = map_lifecycle_to_status(key)
    if mapped is ApiStatus.VERIFIED:
        return ApiStatus.PARTIAL
    return mapped


def project_native_apis(
    *,
    catalog: Iterable[Mapping[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Payloads for built-in OS REST APIs (source=native). Always non-VERIFIED."""
    rows = list(catalog) if catalog is not None else list(_NATIVE_CATALOG)
    out: list[dict[str, Any]] = []
    for row in rows:
        method = str(row.get("method") or "GET").strip().upper()
        path = str(row.get("path") or "").strip()
        capability = str(row.get("capability") or "").strip()
        name = str(row.get("name") or capability or path).strip()
        if not path or not capability:
            continue
        out.append(
            {
                "name": name,
                "method": method,
                "path": path,
                "description": str(row.get("description") or name),
                "source": "native",
                "application_id": "",
                "adapter_id": "",
                "capability": capability,
                "status": ApiStatus.PARTIAL.value,
                "permissions": list(row.get("permissions") or ["system.read"]),
                "authentication_required": bool(row.get("authentication_required", True)),
            }
        )
    return out


def project_adapter_apis(
    adapter: Any,
    *,
    registry_evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Project adapter actions → registry payloads.

    ``registry_evidence`` is keyed by capability (or action name) and may
    supply ``verification_id`` + ``last_verified_at`` for a VERIFIED request.
    Manifest ``action.verification`` alone never yields VERIFIED.
    """
    evidence = registry_evidence or {}
    app_id = str(getattr(adapter, "app_id", "") or "")
    adapter_id = str(getattr(adapter, "id", "") or getattr(adapter, "adapter_id", "") or app_id)
    actions = getattr(adapter, "actions", None) or []
    # Also accept dict-shaped adapter / manifest
    if isinstance(adapter, Mapping):
        app_id = str(adapter.get("app_id") or app_id)
        adapter_id = str(adapter.get("adapter_id") or adapter.get("app_id") or adapter_id)
        actions = adapter.get("actions") or actions

    out: list[dict[str, Any]] = []
    for action in actions:
        if isinstance(action, Mapping):
            name = str(action.get("name") or "").strip()
            description = str(action.get("description") or name)
            params = list(action.get("params") or [])
            risk = str(action.get("risk") or "low")
            verification = action.get("verification")
        else:
            name = str(getattr(action, "name", "") or "").strip()
            description = str(getattr(action, "description", "") or name)
            params = list(getattr(action, "params", []) or [])
            risk = str(getattr(action, "risk", "low") or "low")
            verification = getattr(action, "verification", None)

        if not name:
            continue

        lifecycle = None
        if isinstance(verification, Mapping):
            lifecycle = verification.get("state") or verification.get("status")
        elif isinstance(verification, str):
            lifecycle = verification

        status = _manifest_lifecycle_to_honest_status(
            str(lifecycle) if lifecycle is not None else None
        )
        capability = f"{app_id}.{name}" if app_id else name
        path = f"/v1/apps/{app_id}/actions/{name}" if app_id else f"/v1/actions/{name}"

        payload: dict[str, Any] = {
            "name": description or name,
            "method": "POST",
            "path": path,
            "description": description,
            "source": "virtual_adapter",
            "application_id": app_id,
            "adapter_id": adapter_id,
            "capability": capability,
            "status": status.value,
            "permissions": ["ui.read", "ui.control"],
            "authentication_required": True,
        }
        if risk:
            payload["description"] = f"{description} (risk={risk})"

        ev = evidence.get(capability) or evidence.get(name)
        if isinstance(ev, Mapping):
            vid = ev.get("verification_id")
            lva = ev.get("last_verified_at")
            if vid and lva is not None:
                payload["status"] = ApiStatus.VERIFIED.value
                payload["verification_id"] = vid
                payload["last_verified_at"] = lva

        out.append(payload)
    return out


def project_workflow_apis(
    workflow: Any,
    *,
    registry_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    source: str = "workflow",
) -> list[dict[str, Any]]:
    """Project a workflow/plugin play surface into a registry payload.

    Confidence / risk / embedded verification on the workflow object never
    alone mint VERIFIED.
    """
    evidence = registry_evidence or {}
    if isinstance(workflow, Mapping):
        wf_id = str(workflow.get("id") or "").strip()
        name = str(workflow.get("name") or wf_id or "workflow").strip()
        app_id = str(workflow.get("app_id") or "")
        risk = str(workflow.get("risk") or "low")
        # Optional embedded verification blob — informational only
        verification = workflow.get("verification")
    else:
        wf_id = str(getattr(workflow, "id", "") or "").strip()
        name = str(getattr(workflow, "name", "") or wf_id or "workflow").strip()
        app_id = str(getattr(workflow, "app_id", "") or "")
        risk = str(getattr(workflow, "risk", "low") or "low")
        verification = getattr(workflow, "verification", None)

    if not wf_id:
        return []

    lifecycle = None
    if isinstance(verification, Mapping):
        lifecycle = verification.get("state") or verification.get("status")
    elif isinstance(verification, str):
        lifecycle = verification

    status = _manifest_lifecycle_to_honest_status(
        str(lifecycle) if lifecycle is not None else None
    )
    # Default generated workflow → PARTIAL even without verification blob
    if lifecycle is None:
        status = ApiStatus.PARTIAL

    capability = f"workflow.{wf_id}.play"
    path = f"/v1/workflows/{wf_id}/play"
    src = "plugin" if str(source).strip().lower() == "plugin" else "workflow"

    payload: dict[str, Any] = {
        "name": name,
        "method": "POST",
        "path": path,
        "description": f"Play workflow {name} (risk={risk})",
        "source": src,
        "application_id": app_id,
        "adapter_id": "",
        "capability": capability,
        "status": status.value,
        "permissions": ["automation.execute"],
        "authentication_required": True,
    }

    ev = evidence.get(capability) or evidence.get(wf_id)
    if isinstance(ev, Mapping):
        vid = ev.get("verification_id")
        lva = ev.get("last_verified_at")
        if vid and lva is not None:
            payload["status"] = ApiStatus.VERIFIED.value
            payload["verification_id"] = vid
            payload["last_verified_at"] = lva

    return [payload]


def apply_projections(
    registry: ApiRegistry,
    payloads: Iterable[Mapping[str, Any]],
) -> list[Any]:
    """Register projected payloads; returns the resulting ``ApiRecord`` list."""
    records = []
    for payload in payloads:
        records.append(registry.register(payload))
    return records
