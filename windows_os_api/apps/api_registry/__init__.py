"""N014/N015 — API Registry model, persistence, and projections.

Public surface for in-memory + crash-safe registry. REST catalog (N016) and
execution gateway (N017) remain out of scope.
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
from windows_os_api.apps.api_registry.projections import (
    apply_projections,
    project_adapter_apis,
    project_native_apis,
    project_workflow_apis,
)
from windows_os_api.apps.api_registry.store import (
    ENV_VAR as WINOS_API_REGISTRY_STORE_ENV,
    STORE_FORMAT_VERSION,
    LoadReport,
    PersistentApiRegistry,
    SkippedStore,
    clear_registry_store,
    load_registry,
    save_registry,
    store_dir,
    store_path,
)

__all__ = [
    "API_REGISTRY_SCHEMA_VERSION",
    "ApiRecord",
    "ApiRegistry",
    "ApiStatus",
    "LoadReport",
    "PersistentApiRegistry",
    "RegistrationRejected",
    "STORE_FORMAT_VERSION",
    "SkippedStore",
    "WINOS_API_REGISTRY_STORE_ENV",
    "apply_projections",
    "clear_registry_store",
    "compute_api_id",
    "get_api_registry",
    "load_registry",
    "map_d3_envelope_to_status",
    "map_lifecycle_to_status",
    "project_adapter_apis",
    "project_native_apis",
    "project_workflow_apis",
    "reset_api_registry",
    "save_registry",
    "store_dir",
    "store_path",
]
