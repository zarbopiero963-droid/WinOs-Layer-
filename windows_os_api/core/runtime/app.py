"""FastAPI application factory."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from windows_os_api import __version__
from windows_os_api.api.rest.router import api_router
from windows_os_api.api.websocket.bus import router as ws_router
from windows_os_api.core.runtime.config import Settings, get_settings
from windows_os_api.core.security.audit import get_audit_logger
from windows_os_api.observability.metrics import get_metrics


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        get_audit_logger().log(
            "server.startup",
            subject="system",
            detail={"version": __version__, "host": settings.effective_host()},
        )
        get_metrics().incr("server.starts")
        yield

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
