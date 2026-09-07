"""Aggregate REST router under /v1."""
from __future__ import annotations
from fastapi import APIRouter
from windows_os_api.api.rest import (
    health, processes, apps, windows, ui, filesystem, network, services, workflows, security_routes, ai,
)

api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router)
api_router.include_router(processes.router)
api_router.include_router(apps.router)
api_router.include_router(windows.router)
api_router.include_router(ui.router)
api_router.include_router(filesystem.router)
api_router.include_router(network.router)
api_router.include_router(services.router)
api_router.include_router(workflows.router)
api_router.include_router(security_routes.router)
api_router.include_router(ai.router)
