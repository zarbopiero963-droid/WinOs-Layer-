"""N025 — Deterministic Python SDK export from published/VERIFIED OpenAPI.

Contract (H63-N025 / #67):
* Emit a stdlib ``urllib`` client from a finalized OpenAPI document only.
* Methods map exclusively to operations present in the export — never invent
  endpoints or duplicate the execution engine.
* Deterministic: same OpenAPI → identical SDK source bytes (no timestamps,
  no embedded secrets).
* Client surfaces HTTP 403, supports configurable timeout, and regenerates
  without demoted/PARTIAL operations when the source contract shrinks.
"""
from __future__ import annotations

import re
import types
from typing import Any, Mapping


SDK_SCHEMA_VERSION = "n025-1"
SDK_CLASS_NAME = "WinOsSdkClient"

_IDENT_SAFE = re.compile(r"[^a-zA-Z0-9_]+")
_SECRET_HINTS = (
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
    "bearer",
    "authorization",
)


class SdkExportRejected(ValueError):
    """Raised when OpenAPI input is unsuitable for SDK generation."""


class SdkError(Exception):
    """HTTP or transport failure (also emitted into generated SDK source)."""

    def __init__(self, status_code: int, message: str, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.message = str(message)
        self.body = body


class SdkForbidden(SdkError):
    """HTTP 403 — RBAC / policy denial (fail closed)."""


class SdkTimeout(SdkError):
    """Request exceeded configured timeout."""



def _safe_ident(value: str, *, fallback: str = "op") -> str:
    raw = (value or "").strip()
    cleaned = _IDENT_SAFE.sub("_", raw).strip("_")
    if cleaned and cleaned[0].isdigit():
        cleaned = f"op_{cleaned}"
    return (cleaned or fallback)[:180]


def _iter_operations(doc: Mapping[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Return sorted (path, method, operation) for deterministic emit."""
    paths = doc.get("paths") or {}
    if not isinstance(paths, Mapping):
        raise SdkExportRejected("OpenAPI paths must be an object")
    out: list[tuple[str, str, dict[str, Any]]] = []
    for path in sorted(paths.keys()):
        item = paths[path]
        if not isinstance(item, Mapping):
            continue
        for method in sorted(item.keys()):
            m = str(method).strip().lower()
            if m not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            op = item[method]
            if not isinstance(op, Mapping):
                continue
            out.append((str(path), m, dict(op)))
    return out


def _method_name(op: Mapping[str, Any], *, path: str, method: str) -> str:
    op_id = str(op.get("operationId") or "").strip()
    if op_id:
        return _safe_ident(op_id, fallback="operation")
    frag = path.strip("/").replace("/", "_") or "root"
    return _safe_ident(f"{method}_{frag}", fallback="operation")


def _has_json_body(op: Mapping[str, Any], method: str) -> bool:
    if method in {"get", "head", "options", "delete"}:
        # DELETE may still declare a body; honour requestBody when present.
        if method != "delete" and "requestBody" not in op:
            return False
    return "requestBody" in op


def _assert_no_embedded_secrets(source: str) -> None:
    """Fail closed if generator accidentally baked secret-looking literals."""
    lowered = source.lower()
    # Constructor params and comments mentioning api_key are fine; reject
    # assignment of non-empty string literals to secret-like names.
    for hint in _SECRET_HINTS:
        pattern = re.compile(
            rf"""(?i)({hint})\s*=\s*['\"](?!placeholder)(?!your_)[^'\"]+['\"]"""
        )
        if pattern.search(source):
            raise SdkExportRejected(f"refusing to emit embedded secret-like literal for {hint}")
    # Never ship common env secret values if they somehow appear.
    for banned in ("dev-key-change-me", "admin-key-change-me", "BEGIN PRIVATE KEY"):
        if banned.lower() in lowered:
            raise SdkExportRejected("refusing to emit known secret material in SDK")


def generate_python_sdk(
    openapi_doc: Mapping[str, Any],
    *,
    class_name: str = SDK_CLASS_NAME,
) -> str:
    """Generate deterministic Python client source from a finalized OpenAPI doc.

    Only operations present under ``paths`` are emitted. No fake endpoints.
    """
    if not isinstance(openapi_doc, Mapping):
        raise SdkExportRejected("OpenAPI document must be a mapping")
    if str(openapi_doc.get("openapi") or "")[:1] != "3":
        raise SdkExportRejected("OpenAPI 3.x required for SDK export")
    info = openapi_doc.get("info") or {}
    title = str(info.get("title") or "WinOS API")
    version = str(info.get("version") or "1.0.0")

    ops = _iter_operations(openapi_doc)
    # Stable unique method names
    used: set[str] = set()
    methods_src: list[str] = []
    for path, method, op in ops:
        name = _method_name(op, path=path, method=method)
        base = name
        n = 2
        while name in used:
            name = f"{base}_{n}"
            n += 1
        used.add(name)
        summary = str(op.get("summary") or op.get("operationId") or name).replace("\n", " ")
        has_body = _has_json_body(op, method)
        if has_body:
            sig = f"    def {name}(self, body: dict | None = None, **params: object) -> object:\n"
            body_lines = (
                f'        """{summary} — ``{method.upper()} {path}``."""\n'
                "        payload = dict(body or {})\n"
                "        if params:\n"
                '            nested = dict(payload.get("params") or {})\n'
                "            nested.update(params)\n"
                '            payload["params"] = nested\n'
                f'        return self._request("{method.upper()}", {path!r}, json_body=payload)\n'
            )
        else:
            sig = f"    def {name}(self) -> object:\n"
            body_lines = (
                f'        """{summary} — ``{method.upper()} {path}``."""\n'
                f'        return self._request("{method.upper()}", {path!r})\n'
            )
        methods_src.append(sig + body_lines)

    if not methods_src:
        methods_src.append(
            "    def ping_contract(self) -> dict:\n"
            '        """Empty VERIFIED export — no operations published."""\n'
            '        return {"ok": True, "operations": 0}\n'
        )

    methods_block = "\n".join(methods_src)

    # Fixed header — no timestamps. Title/version from OpenAPI only.
    source = f'''# Auto-generated by WinOS N025 SDK export ({SDK_SCHEMA_VERSION}). DO NOT EDIT.
# Deterministic: same OpenAPI -> identical bytes. Secrets are constructor args only.
# Source contract: {title!r} v{version}
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, MutableMapping

SDK_SCHEMA_VERSION = {SDK_SCHEMA_VERSION!r}
SDK_CLASS_NAME = {class_name!r}


class SdkError(Exception):
    """HTTP or transport failure from the generated client."""

    def __init__(self, status_code: int, message: str, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.message = str(message)
        self.body = body


class SdkForbidden(SdkError):
    """HTTP 403 — RBAC / policy denial (fail closed)."""


class SdkTimeout(SdkError):
    """Request exceeded configured timeout."""


TransportFn = Callable[..., tuple[int, Mapping[str, str], bytes]]


class {class_name}:
    """Thin HTTP client over published/VERIFIED OpenAPI operations only."""

    def __init__(
        self,
        base_url: str = "",
        api_key: str | None = None,
        timeout: float = 30.0,
        transport: TransportFn | None = None,
        client: Any = None,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        # Caller-supplied credential only — never baked into generated source.
        self.api_key = api_key
        self.timeout = float(timeout)
        self._transport = transport
        self._client = client  # duck-typed httpx/TestClient-like .request()

    def _headers(self) -> dict[str, str]:
        headers = {{"Accept": "application/json"}}
        if self.api_key:
            headers["X-API-Key"] = str(self.api_key)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        json_body: Mapping[str, Any] | None = None,
    ) -> Any:
        url = f"{{self.base_url}}{{path}}"
        headers: MutableMapping[str, str] = dict(self._headers())
        data: bytes | None = None
        if json_body is not None:
            data = json.dumps(json_body, separators=(",", ":"), sort_keys=True).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            if self._transport is not None:
                status, _resp_headers, raw = self._transport(
                    method, url, dict(headers), data, self.timeout
                )
            elif self._client is not None:
                kwargs: dict[str, Any] = {{"headers": dict(headers), "timeout": self.timeout}}
                if data is not None:
                    kwargs["content"] = data
                resp = self._client.request(method, url, **kwargs)
                status = int(resp.status_code)
                raw = resp.content if hasattr(resp, "content") else bytes(resp.read())
            else:
                req = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        status = int(getattr(resp, "status", 200))
                        raw = resp.read()
                except urllib.error.HTTPError as exc:
                    status = int(exc.code)
                    raw = exc.read() if hasattr(exc, "read") else b""
        except (TimeoutError, socket.timeout) as exc:
            raise SdkTimeout(408, f"timeout after {{self.timeout}}s", None) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise SdkTimeout(408, f"timeout after {{self.timeout}}s", None) from exc
            raise SdkError(0, str(exc), None) from exc

        parsed: Any
        try:
            parsed = json.loads(raw.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = raw.decode("utf-8", errors="replace")

        if status == 403:
            detail = parsed.get("detail") if isinstance(parsed, dict) else parsed
            raise SdkForbidden(403, str(detail or "forbidden"), parsed)
        if status >= 400:
            detail = parsed.get("detail") if isinstance(parsed, dict) else parsed
            raise SdkError(status, str(detail or f"HTTP {{status}}"), parsed)
        return parsed

{methods_block}
'''
    # Normalize newlines and ensure trailing newline for byte stability.
    source = source.replace("\r\n", "\n").rstrip() + "\n"
    _assert_no_embedded_secrets(source)
    return source


def canonical_sdk_bytes(openapi_doc: Mapping[str, Any]) -> bytes:
    """UTF-8 bytes of the deterministic SDK source."""
    return generate_python_sdk(openapi_doc).encode("utf-8")


def load_generated_sdk(
    source: str,
    *,
    module_name: str = "winos_n025_generated_sdk",
):
    """Exec generated source into an isolated module (test helper).

    Does not invent endpoints — only loads what ``generate_python_sdk`` emitted.
    """
    if not isinstance(source, str) or "class " not in source:
        raise SdkExportRejected("invalid generated SDK source")
    module = types.ModuleType(module_name)
    # Restricted-ish exec: generated code is ours; still no secrets expected.
    exec(compile(source, module_name, "exec"), module.__dict__)  # noqa: S102
    client_cls = getattr(module, SDK_CLASS_NAME, None)
    if client_cls is None:
        # Allow alternate class name if generator was parameterized.
        for attr in module.__dict__.values():
            if isinstance(attr, type) and attr.__name__.endswith("SdkClient"):
                client_cls = attr
                break
    if client_cls is None:
        raise SdkExportRejected("generated module missing SDK client class")
    module.CLIENT_CLASS = client_cls  # type: ignore[attr-defined]
    return module


def sdk_from_openapi(openapi_doc: Mapping[str, Any]):
    """Generate + load client class from OpenAPI (convenience for tests)."""
    source = generate_python_sdk(openapi_doc)
    mod = load_generated_sdk(source)
    return mod.CLIENT_CLASS, source
