"""N019 — Deterministic OpenAPI export, rich operation schema, validation.

Contract (H63-N019 / #67):
* Stable canonical JSON bytes (prefer **zero** volatile fields).
* Rich per-operation schema: input / output / errors / scopes / risk / auth / version.
* Unique ``operationId`` values (include app_id / api_id).
* CRUD / path publication only for **verified** capabilities.
* Invalid documents are rejected (no publish/export of garbage).
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

OPENAPI_SCHEMA_VERSION = "n019-1"
OPENAPI_DOC_VERSION = "1.0.0"

# Documented volatile field names (none used by default exporters).
# If a future exporter adds one, strip it before byte-compare in tests.
VOLATILE_OPENAPI_FIELDS = frozenset({"x-generated-at", "x-exported-at", "generatedAt"})

_OP_ID_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")


class OpenAPISchemaRejected(ValueError):
    """Raised when an OpenAPI document violates N019 structural rules."""


def _safe_op_fragment(value: str, *, fallback: str = "op") -> str:
    raw = (value or "").strip()
    cleaned = _OP_ID_SAFE.sub("_", raw).strip("_")
    return (cleaned or fallback)[:120]


def make_operation_id(*, app_id: str, action_name: str) -> str:
    """Globally unique operationId for per-app Virtual API actions."""
    return f"app__{_safe_op_fragment(app_id, fallback='app')}__{_safe_op_fragment(action_name)}"


def make_registry_operation_id(*, api_id: str, method: str, name: str) -> str:
    """Globally unique operationId for registry-backed CRUD paths."""
    return (
        f"api__{_safe_op_fragment(api_id, fallback='api')}"
        f"__{_safe_op_fragment(method.lower(), fallback='post')}"
        f"__{_safe_op_fragment(name)}"
    )


def crud_kind_for_control(control_type: str | None, action_name: str = "") -> str:
    """Map verified UI control → CRUD semantic label (not inventing HTTP verbs).

    The live invoke route remains POST; ``x-crud`` documents intent for clients.
    """
    ct = (control_type or "").strip().casefold()
    name = (action_name or "").casefold()
    if ct == "edit" or name.startswith("set_"):
        return "update"
    if ct == "button":
        if any(k in name for k in ("create", "new", "add", "save")):
            return "create"
        if any(k in name for k in ("delete", "remove", "destroy")):
            return "delete"
        return "execute"
    if ct == "menuitem":
        return "execute"
    return "execute"


def _param_property(name: str) -> dict[str, Any]:
    # Edit fields use ``value``; keep string with description (richer than bare type).
    if name == "value":
        return {
            "type": "string",
            "description": "New control value to write (verified Update capability)",
            "minLength": 0,
        }
    return {"type": "string", "description": f"Parameter {name}"}


def build_action_operation(
    *,
    app_id: str,
    action_name: str,
    description: str,
    params: list[str],
    risk: str,
    control_type: str | None,
    permissions: Iterable[str] | None = None,
    authentication_required: bool = True,
    verification_id: str | None = None,
) -> dict[str, Any]:
    """Rich OpenAPI 3 operation object for a verified adapter action."""
    props = {p: _param_property(p) for p in params}
    params_schema: dict[str, Any] = {
        "type": "object",
        "properties": props,
        "additionalProperties": False,
    }
    if params:
        params_schema["required"] = list(params)

    body_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"params": params_schema},
        "additionalProperties": False,
    }
    if params:
        body_schema["required"] = ["params"]

    scopes = list(permissions) if permissions is not None else ["adapter.use"]
    error_schema = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "block_kind": {"type": "string", "enum": ["security", "operational", ""]},
            "error": {"type": "string"},
            "denied": {"type": "boolean"},
        },
        "additionalProperties": True,
    }
    success_schema = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "action": {"type": "string"},
            "set_value": {"type": "string"},
            "clicked": {"type": "string"},
            "element": {"type": "string"},
        },
        "required": ["ok"],
        "additionalProperties": True,
    }

    security: list[dict[str, list[str]]] = []
    if authentication_required:
        security = [{"ApiKeyAuth": []}]

    op = {
        "summary": description or action_name,
        "operationId": make_operation_id(app_id=app_id, action_name=action_name),
        "tags": [app_id],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": body_schema}},
        },
        "responses": {
            "200": {
                "description": "Action result (ok alone is not VERIFIED proof — see N018)",
                "content": {"application/json": {"schema": success_schema}},
            },
            "400": {
                "description": "Invalid request / schema",
                "content": {"application/json": {"schema": error_schema}},
            },
            "403": {
                "description": "Denied by RBAC or sandbox policy",
                "content": {"application/json": {"schema": error_schema}},
            },
            "404": {
                "description": "Adapter or action not found (operational)",
                "content": {"application/json": {"schema": error_schema}},
            },
            "409": {
                "description": "Conflict / not bound / execution blocked",
                "content": {"application/json": {"schema": error_schema}},
            },
            "503": {
                "description": "Registry or dependency unavailable",
                "content": {"application/json": {"schema": error_schema}},
            },
        },
        "security": security,
        "x-risk": risk or "low",
        "x-scopes": scopes,
        "x-permissions": scopes,
        "x-auth": {
            "required": bool(authentication_required),
            "schemes": ["ApiKeyAuth"] if authentication_required else [],
        },
        "x-version": OPENAPI_DOC_VERSION,
        "x-schema-version": OPENAPI_SCHEMA_VERSION,
        "x-verification-state": "VERIFIED",
        "x-crud": crud_kind_for_control(control_type, action_name),
        "x-control-type": control_type or "",
        "x-app-id": app_id,
    }
    vid = (verification_id or "").strip()
    if vid:
        op["x-verification-id"] = vid
    return op


def build_registry_operation(record: Mapping[str, Any]) -> dict[str, Any]:
    """Rich operation for a VERIFIED registry API record (real method/path)."""
    api_id = str(record.get("id") or "")
    name = str(record.get("name") or api_id)
    method = str(record.get("method") or "POST").strip().upper()
    description = str(record.get("description") or name)
    permissions = list(record.get("permissions") or [])
    auth_req = bool(record.get("authentication_required", True))
    application_id = str(record.get("application_id") or "")
    capability = str(record.get("capability") or "")
    source = str(record.get("source") or "")

    # Map HTTP method → CRUD label for verified registry entries only.
    crud_map = {
        "POST": "create",
        "GET": "read",
        "PUT": "update",
        "PATCH": "update",
        "DELETE": "delete",
    }
    crud = crud_map.get(method, "execute")

    error_schema = {
        "type": "object",
        "properties": {
            "detail": {},
            "code": {"type": "string"},
            "error": {"type": "string"},
        },
        "additionalProperties": True,
    }
    success_schema = {
        "type": "object",
        "description": "Registry-backed capability result",
        "additionalProperties": True,
    }

    op: dict[str, Any] = {
        "summary": description,
        "operationId": make_registry_operation_id(api_id=api_id, method=method, name=name),
        "tags": [application_id or source or "registry"],
        "responses": {
            "200": {
                "description": "Success envelope",
                "content": {"application/json": {"schema": success_schema}},
            },
            "400": {
                "description": "Invalid request",
                "content": {"application/json": {"schema": error_schema}},
            },
            "403": {
                "description": "Forbidden",
                "content": {"application/json": {"schema": error_schema}},
            },
            "404": {
                "description": "Not found",
                "content": {"application/json": {"schema": error_schema}},
            },
            "503": {
                "description": "Registry unavailable",
                "content": {"application/json": {"schema": error_schema}},
            },
        },
        "security": [{"ApiKeyAuth": []}] if auth_req else [],
        "x-risk": "medium",
        "x-scopes": permissions,
        "x-permissions": permissions,
        "x-auth": {
            "required": auth_req,
            "schemes": ["ApiKeyAuth"] if auth_req else [],
        },
        "x-version": OPENAPI_DOC_VERSION,
        "x-schema-version": OPENAPI_SCHEMA_VERSION,
        "x-verification-state": "VERIFIED",
        "x-crud": crud,
        "x-api-id": api_id,
        "x-capability": capability,
        "x-source": source,
        "x-application-id": application_id,
    }
    vid = str(record.get("verification_id") or "").strip()
    if vid:
        op["x-verification-id"] = vid
    if method in {"POST", "PUT", "PATCH"}:
        op["requestBody"] = {
            "required": False,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "params": {
                                "type": "object",
                                "additionalProperties": True,
                            }
                        },
                        "additionalProperties": True,
                    }
                }
            },
        }
    return op


def _security_schemes() -> dict[str, Any]:
    return {
        "ApiKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "WinOS API key (RBAC + app scopes enforced before effect)",
        }
    }


def assemble_openapi_document(
    *,
    title: str,
    paths: Mapping[str, Any],
    description: str = "",
) -> dict[str, Any]:
    """Build a top-level OpenAPI 3.0.3 document with stable key layout."""
    ordered_paths = {k: paths[k] for k in sorted(paths.keys())}
    info: dict[str, Any] = {
        "title": title,
        "version": OPENAPI_DOC_VERSION,
        "x-schema-version": OPENAPI_SCHEMA_VERSION,
    }
    if description:
        info["description"] = description
    return {
        "openapi": "3.0.3",
        "info": info,
        "paths": ordered_paths,
        "components": {"securitySchemes": _security_schemes()},
    }


def strip_volatile_fields(doc: Any) -> Any:
    """Remove documented volatile keys recursively (identity if none present)."""
    if isinstance(doc, dict):
        return {
            k: strip_volatile_fields(v)
            for k, v in doc.items()
            if k not in VOLATILE_OPENAPI_FIELDS
        }
    if isinstance(doc, list):
        return [strip_volatile_fields(x) for x in doc]
    return doc


def canonical_openapi_bytes(doc: Mapping[str, Any] | dict[str, Any]) -> bytes:
    """Deterministic UTF-8 JSON: sorted keys, compact separators, no volatiles."""
    cleaned = strip_volatile_fields(dict(doc))
    text = json.dumps(
        cleaned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text.encode("utf-8")


def validate_openapi_document(doc: Any) -> dict[str, Any]:
    """Fail-closed structural validation. Returns the same dict if valid."""
    if not isinstance(doc, dict):
        raise OpenAPISchemaRejected("OpenAPI document must be an object")
    if doc.get("openapi") not in {"3.0.0", "3.0.1", "3.0.2", "3.0.3", "3.1.0"}:
        raise OpenAPISchemaRejected("missing or unsupported openapi version")
    info = doc.get("info")
    if not isinstance(info, dict) or not str(info.get("title") or "").strip():
        raise OpenAPISchemaRejected("info.title is required")
    if not str(info.get("version") or "").strip():
        raise OpenAPISchemaRejected("info.version is required")
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        raise OpenAPISchemaRejected("paths must be an object")

    seen_ids: dict[str, str] = {}
    http_verbs = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
    for path, item in paths.items():
        if not isinstance(path, str) or not path.startswith("/"):
            raise OpenAPISchemaRejected(f"invalid path key: {path!r}")
        if not isinstance(item, dict):
            raise OpenAPISchemaRejected(f"path item must be object: {path}")
        for verb, operation in item.items():
            if verb.startswith("x-") or verb in {"parameters", "summary", "description", "servers"}:
                continue
            if verb not in http_verbs:
                raise OpenAPISchemaRejected(f"unsupported verb {verb!r} on {path}")
            if not isinstance(operation, dict):
                raise OpenAPISchemaRejected(f"operation must be object: {verb.upper()} {path}")
            op_id = operation.get("operationId")
            if not isinstance(op_id, str) or not op_id.strip():
                raise OpenAPISchemaRejected(
                    f"operationId required for {verb.upper()} {path}"
                )
            if op_id in seen_ids:
                raise OpenAPISchemaRejected(
                    f"duplicate operationId {op_id!r} "
                    f"({seen_ids[op_id]} and {verb.upper()} {path})"
                )
            seen_ids[op_id] = f"{verb.upper()} {path}"
            responses = operation.get("responses")
            if not isinstance(responses, dict) or not responses:
                raise OpenAPISchemaRejected(
                    f"responses required for {verb.upper()} {path}"
                )
            # Rich contract: verified ops must advertise risk/scopes/auth/version.
            for field in ("x-risk", "x-scopes", "x-auth", "x-version"):
                if field not in operation:
                    raise OpenAPISchemaRejected(
                        f"{field} required for {verb.upper()} {path}"
                    )
            if not isinstance(operation.get("x-scopes"), list):
                raise OpenAPISchemaRejected(
                    f"x-scopes must be a list for {verb.upper()} {path}"
                )
            auth = operation.get("x-auth")
            if not isinstance(auth, dict) or "required" not in auth:
                raise OpenAPISchemaRejected(
                    f"x-auth.required required for {verb.upper()} {path}"
                )
    return doc


def finalize_openapi_export(doc: Mapping[str, Any] | dict[str, Any]) -> dict[str, Any]:
    """Validate + return a canonical dict suitable for HTTP JSONResponse."""
    validated = validate_openapi_document(dict(doc))
    # Re-parse from canonical bytes so key order is stable for dict consumers
    # that preserve insertion order from json.loads.
    raw = canonical_openapi_bytes(validated)
    return json.loads(raw.decode("utf-8"))




def is_action_verified_for_openapi(verification: Any) -> bool:
    """N018+N019 gate: VERIFIED + non-empty verification_id."""
    return (
        isinstance(verification, dict)
        and verification.get("state") == "VERIFIED"
        and isinstance(verification.get("verification_id"), str)
        and bool(str(verification.get("verification_id")).strip())
    )
