"""FastAPI application factory."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from windows_os_api import __version__
from windows_os_api.api.rest.router import api_router
from windows_os_api.api.websocket.bus import router as ws_router
from windows_os_api.apps.adapters.validation import AppIdRejected
from windows_os_api.core.runtime.config import Settings, get_settings
from windows_os_api.core.security.audit import get_audit_logger
from windows_os_api.observability.metrics import get_metrics


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            from windows_os_api.apps.ai.provider import sync_llm_bridge
            sync_llm_bridge()
        except Exception:  # noqa: BLE001
            pass
        # Gli adapter salvati tornano in memoria all'avvio: e' questo passo che
        # rende vera la parola «persistente», senza il quale il manifest sarebbe
        # un file che nessuno rilegge. Tornano NON agganciati — la descrizione
        # sopravvive, il legame con la finestra no (vedi `adapters/store.py`).
        adapters_report: dict[str, list] = {"restored": [], "skipped": []}
        try:
            from windows_os_api.apps.adapters.engine import load_persisted_adapters

            adapters_report = load_persisted_adapters()
        except Exception as exc:  # noqa: BLE001
            # Un problema nel ripristino non impedisce al server di partire, ma
            # non sparisce: finisce nell'audit di avvio come il resto.
            adapters_report = {"restored": [], "skipped": [{"reason": str(exc)}]}

        key_lists = (
            settings.admin_api_keys,
            settings.api_keys,
            settings.operator_api_keys,
            settings.viewer_api_keys,
        )
        configured_keys = sum(1 for lst in key_lists for k in (lst or []) if k)
        get_audit_logger().log(
            "server.startup",
            subject="system",
            detail={
                "version": __version__,
                "host": settings.effective_host(),
                "adapters_restored": adapters_report["restored"],
                "adapters_skipped": adapters_report["skipped"],
                "require_auth": settings.require_auth,
                "configured_api_keys": configured_keys,
            },
        )
        get_metrics().incr("server.starts")
        try:
            yield
        finally:
            # A service stop is only graceful if the ASGI lifespan reaches this
            # point. The Windows hard smoke reads this durable event after SCM
            # reports STOPPED, distinguishing CTRL_C_EVENT shutdown from a
            # supervisor that merely killed the process tree.
            get_audit_logger().log(
                "server.shutdown",
                subject="system",
                detail={"version": __version__},
            )

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    origins = [f"http://{h}:{settings.port}" for h in settings.allowed_hosts]
    origins += [f"http://{h}" for h in settings.allowed_hosts]
    if settings.remote_access_enabled:
        # N013: remoto opt-in uses explicit Origin allowlist — never implicit "*".
        cors_origins = list(settings.cors_allowed_origins) or list(origins)
    else:
        cors_origins = origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # N013 process-wide limiter/gate (shared with deps.reset_limiter for tests).
    from windows_os_api.api.rest.deps import get_concurrency_gate, get_limiter

    _LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})

    def _peer_host(request: Request) -> str:
        """TCP peer only. X-Forwarded-For / Forwarded must never invent loopback."""
        return request.client.host if request.client else ""

    @app.middleware("http")
    async def metrics_and_remote_guard(request: Request, call_next):
        # --- body cap (Content-Length) ---
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > settings.max_body_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request body too large"},
                    )
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})

        peer = _peer_host(request)
        # Forwarded headers are observed only to refuse spoof-as-loopback claims
        # from a non-loopback peer (never used to *grant* access).
        forwarded = (
            request.headers.get("x-forwarded-for")
            or request.headers.get("forwarded")
            or ""
        ).lower()
        claims_loopback = any(
            token in forwarded
            for token in ("127.0.0.1", "::1", "localhost", "for=127.", "for=\"[::1]\"")
        )

        if not settings.remote_access_enabled:
            if peer and peer not in _LOOPBACK and peer not in settings.allowed_hosts:
                return Response("Remote access disabled", status_code=403)
            # Non-loopback peer claiming forwarded loopback → still denied (and explicit).
            if peer and peer not in _LOOPBACK and claims_loopback:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Forwarded loopback claim rejected"},
                )
        else:
            # Remoto opt-in: still never treat forwarded loopback as identity.
            if peer and peer not in _LOOPBACK and claims_loopback:
                # Ignore claim; continue with peer-based policy (allowed via remote flag).
                pass

        # --- rate limit (peer + key prefix) ---
        limiter = get_limiter(settings)
        api_key = request.headers.get("x-api-key") or ""
        rate_key = f"peer:{peer}|k:{api_key[:8] if api_key else '-'}"
        if not limiter.allow(rate_key):
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
            )

        # --- concurrency quota ---
        gate = get_concurrency_gate(settings)
        if not gate.try_acquire():
            return JSONResponse(
                status_code=429,
                content={"detail": "Concurrency limit exceeded"},
            )

        metrics = get_metrics()
        metrics.incr("http.requests")
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            gate.release()
        elapsed = (time.perf_counter() - start) * 1000
        metrics.timing("http.latency_ms", elapsed)
        response.headers["X-WinOs-Version"] = __version__
        return response

    @app.exception_handler(AppIdRejected)
    async def app_id_rejected(request: Request, exc: AppIdRejected) -> Response:
        """A blank `app_id` is a bad request, not a server fault.

        Pydantic answers 422 when the field is *missing*; `""` and `"   "` pass
        its type check and are refused deeper, at `create_adapter`. Without this
        handler that refusal escaped as an unhandled exception — a 500, which
        tells the caller the server broke when in fact their request did.
        Registered once here rather than caught in each route, for the same
        reason the check itself is not in the routes.
        """
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    app.include_router(api_router)
    app.include_router(ws_router)

    # N019: merge VERIFIED registry paths into root /openapi.json (S61-08 / L9/W7).
    def custom_openapi():
        # Rebuild each call so newly VERIFIED registry APIs appear (N019).
        from fastapi.openapi.utils import get_openapi

        from windows_os_api.api.rest.apis import _registry_openapi_document
        from windows_os_api.apps.schema.openapi_export import (
            merge_registry_paths_into_fastapi_schema,
        )

        base = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        try:
            registry_doc = _registry_openapi_document(visible_app_ids=None)
            base = merge_registry_paths_into_fastapi_schema(base, registry_doc)
        except Exception:  # noqa: BLE001 — never break /openapi.json on registry issues
            pass
        return base

    app.openapi = custom_openapi  # type: ignore[method-assign]

    cc_dir = Path(__file__).resolve().parents[2] / "control_center"
    index = cc_dir / "index.html"
    if index.exists():

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def control_center():
            return index.read_text(encoding="utf-8")

    app.state.settings = settings
    return app
