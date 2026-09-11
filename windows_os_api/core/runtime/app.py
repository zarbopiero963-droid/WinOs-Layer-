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

        get_audit_logger().log(
            "server.startup",
            subject="system",
            detail={
                "version": __version__,
                "host": settings.effective_host(),
                "adapters_restored": adapters_report["restored"],
                "adapters_skipped": adapters_report["skipped"],
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins if not settings.remote_access_enabled else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def metrics_and_remote_guard(request: Request, call_next):
        if not settings.remote_access_enabled:
            client = request.client.host if request.client else ""
            if client not in ("127.0.0.1", "::1", "localhost", "testclient"):
                if client and client not in settings.allowed_hosts:
                    return Response("Remote access disabled", status_code=403)
        metrics = get_metrics()
        metrics.incr("http.requests")
        start = time.perf_counter()
        response = await call_next(request)
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

    cc_dir = Path(__file__).resolve().parents[2] / "control_center"
    index = cc_dir / "index.html"
    if index.exists():

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def control_center():
            return index.read_text(encoding="utf-8")

    app.state.settings = settings
    return app
