"""Shared fixtures — real TestClient against FakeBackend.

N002: also reset bus/metrics/rate-limiter and wipe adapter store between tests;
expose live helpers that fail-closed on FakeBackend.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Force fake backend + known keys before imports mutate cache
os.environ["WINOS_BACKEND"] = "fake"
os.environ["WINOS_REQUIRE_AUTH"] = "true"
# Suite may use documentation placeholder strings; release path refuses them (N011).
os.environ["WINOS_ALLOW_PLACEHOLDER_API_KEYS"] = "true"
os.environ["WINOS_API_KEYS"] = '["dev-key-change-me"]'
os.environ["WINOS_ADMIN_API_KEYS"] = '["admin-key-change-me"]'
os.environ["WINOS_VIEWER_API_KEYS"] = '["viewer-key-n011"]'
os.environ["WINOS_OPERATOR_API_KEYS"] = '["operator-key-n011"]'
# Gli adapter ora si salvano su disco, e il loro default e' la cartella di
# configurazione dell'utente: senza questo, far girare la suite lascerebbe
# manifest nel profilo di chi la lancia. Impostato prima degli import, come il
# backend, perche' `store_dir()` legge l'ambiente a ogni chiamata.
os.environ.setdefault(
    "WINOS_ADAPTER_STORE", str(Path(tempfile.mkdtemp(prefix="winos-adapters-")))
)

from windows_os_api.core.runtime.config import get_settings
from windows_os_api.backends.factory import reset_backend, get_backend
from windows_os_api.backends.fake import FakeBackend
from windows_os_api.apps.adapters.engine import reset_adapters
from windows_os_api.apps.adapters.store import clear_adapter_store
from windows_os_api.apps.workflows.recorder import reset_workflows
from windows_os_api.apps.sandbox.permissions import reset_policies
from windows_os_api.core.security.audit import reset_audit_logger
from windows_os_api.core.runtime.app import create_app
from windows_os_api.apps.ai.settings_store import reset_ai_settings, set_config_path_override
from windows_os_api.apps.ai.provider import reset_ai_client
from windows_os_api.apps.reasoning.offline import set_llm, NullLLM
from windows_os_api.core.events.bus import reset_event_bus
from windows_os_api.observability.metrics import reset_metrics
from windows_os_api.api.rest.deps import reset_limiter


def _reset_shared_singletons() -> None:
    """Full isolation surface for tmp_sandbox / live fixtures (N002)."""
    get_settings.cache_clear()
    reset_backend()
    reset_adapters()
    reset_workflows()
    reset_policies()
    reset_audit_logger()
    reset_event_bus()
    reset_metrics()
    reset_limiter()
    clear_adapter_store()


def ensure_live_backend():
    """Fail-closed: a live fixture must never resolve to FakeBackend."""
    backend = get_backend()
    if isinstance(backend, FakeBackend):
        raise RuntimeError(
            "live fixture rejects FakeBackend "
            "(set WINOS_BACKEND to windows/linux and WINOS_ALLOW_FAKE_FALLBACK=false)"
        )
    return backend


def assert_not_fake_backend(backend) -> None:
    """Helper for unit tests: raise if *backend* is FakeBackend."""
    if isinstance(backend, FakeBackend):
        raise RuntimeError("live fixture rejects FakeBackend")


@pytest.fixture()
def tmp_sandbox(tmp_path, monkeypatch):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    audit = tmp_path / "audit.jsonl"
    ai_cfg = tmp_path / "ai_settings.json"
    adapter_store = tmp_path / "adapters"
    adapter_store.mkdir()
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(sandbox))
    monkeypatch.setenv("WINOS_AUDIT_LOG_PATH", str(audit))
    monkeypatch.setenv("WINOS_BACKEND", "fake")
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(adapter_store))
    monkeypatch.setenv("WINOS_AI_PROVIDER", "local")
    monkeypatch.setenv("WINOS_AI_API_KEY", "")
    monkeypatch.delenv("WINOS_AI_MODEL", raising=False)
    monkeypatch.delenv("WINOS_AI_BASE_URL", raising=False)
    set_config_path_override(ai_cfg)
    reset_ai_settings()
    reset_ai_client()
    set_llm(NullLLM())
    _reset_shared_singletons()
    yield sandbox
    set_config_path_override(None)
    reset_ai_settings()
    reset_ai_client()
    set_llm(NullLLM())
    _reset_shared_singletons()


@pytest.fixture()
def client(tmp_sandbox):
    settings = get_settings()
    assert settings.resolve_backend() == "fake"
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def live_backend(tmp_path, monkeypatch):
    """Real OS backend fixture — fail-closed if FakeBackend appears."""
    kind = "windows" if sys.platform == "win32" else "linux"
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    adapter_store = tmp_path / "adapters"
    adapter_store.mkdir()
    monkeypatch.setenv("WINOS_BACKEND", kind)
    monkeypatch.setenv("WINOS_ALLOW_FAKE_FALLBACK", "false")
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(sandbox))
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(adapter_store))
    _reset_shared_singletons()
    try:
        yield ensure_live_backend()
    finally:
        _reset_shared_singletons()


@pytest.fixture()
def live_client(live_backend):
    """TestClient bound to a non-fake backend (same fail-closed rule)."""
    settings = get_settings()
    assert settings.resolve_backend() != "fake"
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers():
    return {"X-API-Key": "dev-key-change-me"}


@pytest.fixture()
def admin_headers():
    return {"X-API-Key": "admin-key-change-me"}


@pytest.fixture()
def viewer_headers():
    return {"X-API-Key": "viewer-key-n011"}


@pytest.fixture()
def operator_headers():
    return {"X-API-Key": "operator-key-n011"}
