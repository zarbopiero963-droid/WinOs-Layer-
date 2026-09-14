"""N014 — API Registry model and authoritative #61 states.

Public surface for the in-memory registry. Persistence (N015), REST catalog
(N016), and execution gateway (N017) are out of scope here.
"""
from __future__ import annotations

from windows_os_api.apps.api_registry.model import (
    API_REGISTRY_SCHEMA_VERSION,
    ApiRecord,
    ApiRegistry,
    ApiStatus,
    RegistrationRejected,
    compute_api_id,
    get_api_registry,
    map_d3_envelope_to_status,
    map_lifecycle_to_status,
    reset_api_registry,
)

__all__ = [
    "API_REGISTRY_SCHEMA_VERSION",
    "ApiRecord",
    "ApiRegistry",
    "ApiStatus",
    "RegistrationRejected",
    "compute_api_id",
    "get_api_registry",
    "map_d3_envelope_to_status",
    "map_lifecycle_to_status",
    "reset_api_registry",
]
