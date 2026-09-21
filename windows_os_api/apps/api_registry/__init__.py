"""N014–N019 — API Registry model, persistence, projections, catalog, gateway, API Test.

Public surface for in-memory + crash-safe registry, read-only catalog queries,
the shared execution gateway (authorize + execute; no implicit create), and
N018 API Test (execution + independent postcondition + verification_id).
"""
from __future__ import annotations

from windows_os_api.apps.api_registry.api_test import (
    observe_edit_value,
    run_api_test,
)
from windows_os_api.apps.api_registry.catalog import (
    CatalogPage,
    CatalogScopeDenied,
    RegistryUnavailable,
    filter_records,
    get_catalog_record,
    list_catalog,
    resolve_registry,
)
from windows_os_api.apps.api_registry.gateway import (
    AuthorizationDecision,
    ExecutionCode,
    authorize_execution,
    execute_via_gateway,
    find_api_for_action,
    gateway_http_status,
)
from windows_os_api.apps.api_registry.model import (
    API_REGISTRY_SCHEMA_VERSION,
    ApiRecord,
    ApiRegistry,
    ApiStatus,
    RegistrationRejected,
    clear_verification_proofs,
    compute_api_id,
    get_api_registry,
    has_verification_proof,
    issue_verification_proof,
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
    "AuthorizationDecision",
    "CatalogPage",
    "CatalogScopeDenied",
    "ExecutionCode",
    "LoadReport",
    "PersistentApiRegistry",
    "RegistrationRejected",
    "RegistryUnavailable",
    "STORE_FORMAT_VERSION",
    "SkippedStore",
    "WINOS_API_REGISTRY_STORE_ENV",
    "apply_projections",
    "authorize_execution",
    "clear_registry_store",
    "compute_api_id",
    "execute_via_gateway",
    "filter_records",
    "find_api_for_action",
    "gateway_http_status",
    "get_api_registry",
    "has_verification_proof",
    "issue_verification_proof",
    "clear_verification_proofs",
    "get_catalog_record",
    "list_catalog",
    "load_registry",
    "map_d3_envelope_to_status",
    "map_lifecycle_to_status",
    "project_adapter_apis",
    "project_native_apis",
    "project_workflow_apis",
    "reset_api_registry",
    "resolve_registry",
    "save_registry",
    "store_dir",
    "store_path",
    "observe_edit_value",
    "run_api_test",
]
