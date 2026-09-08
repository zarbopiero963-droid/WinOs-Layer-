"""Network interfaces / connections."""
from __future__ import annotations
from typing import Any
from windows_os_api.backends.factory import get_backend

def interfaces() -> list[dict[str, Any]]:
    return get_backend().network_interfaces()

def connections() -> list[dict[str, Any]]:
    return get_backend().network_connections()

# Probes. Validation lives in os/network/validation.py and is applied by each
# backend at the point that acts, so a caller reaching a backend directly gets
# the same answer as one coming through here.
def routes() -> list[dict[str, Any]]:
    return get_backend().list_routes()

def dns_resolve(host: str) -> dict[str, Any]:
    return get_backend().dns_resolve(host)

def dns_reverse(address: str) -> dict[str, Any]:
    return get_backend().dns_reverse(address)

def ping(host: str, count: int = 2, timeout: int = 2) -> dict[str, Any]:
    return get_backend().ping(host, count, timeout)
