"""Shared fixtures — real TestClient against FakeBackend."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Force fake backend + known keys before imports mutate cache
os.environ["WINOS_BACKEND"] = "fake"
os.environ["WINOS_REQUIRE_AUTH"] = "true"
os.environ["WINOS_API_KEYS"] = '["dev-key-change-me"]'
os.environ["WINOS_ADMIN_API_KEYS"] = '["admin-key-change-me"]'
# Gli adapter ora si salvano su disco, e il loro default e' la cartella di
# configurazione dell'utente: senza questo, far girare la suite lascerebbe
# manifest nel profilo di chi la lancia. Impostato prima degli import, come il
# backend, perche' `store_dir()` legge l'ambiente a ogni chiamata.
os.environ.setdefault(
    "WINOS_ADAPTER_STORE", str(Path(tempfile.mkdtemp(prefix="winos-adapters-")))
)

from windows_os_api.core.runtime.config import get_settings
from windows_os_api.backends.factory import reset_backend
from windows_os_api.apps.adapters.engine import reset_adapters
from windows_os_api.apps.workflows.recorder import reset_workflows
from windows_os_api.apps.sandbox.permissions import reset_policies
from windows_os_api.core.security.audit import reset_audit_logger, AuditLogger
from windows_os_api.core.runtime.app import create_app
from windows_os_api.apps.ai.settings_store import reset_ai_settings, set_config_path_override
from windows_os_api.apps.ai.provider import reset_ai_client
from windows_os_api.apps.reasoning.offline import set_llm, NullLLM


@pytest.fixture()
def tmp_sandbox(tmp_path, monkeypatch):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    audit = tmp_path / "audit.jsonl"
    ai_cfg = tmp_path / "ai_settings.json"
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(sandbox))
    monkeypatch.setenv("WINOS_AUDIT_LOG_PATH", str(audit))
    monkeypatch.setenv("WINOS_BACKEND", "fake")
    monkeypatch.setenv("WINOS_AI_PROVIDER", "local")
    monkeypatch.setenv("WINOS_AI_API_KEY", "")
    monkeypatch.delenv("WINOS_AI_MODEL", raising=False)
    monkeypatch.delenv("WINOS_AI_BASE_URL", raising=False)
    get_settings.cache_clear()
    set_config_path_override(ai_cfg)
    reset_ai_settings()
    reset_ai_client()
    set_llm(NullLLM())
    reset_backend()
    reset_adapters()
    reset_workflows()
    reset_policies()
    reset_audit_logger()
    yield sandbox
    get_settings.cache_clear()
    set_config_path_override(None)
    reset_ai_settings()
    reset_ai_client()
    set_llm(NullLLM())
    reset_backend()
    reset_adapters()
    reset_workflows()
    reset_policies()
    reset_audit_logger()


@pytest.fixture()
def client(tmp_sandbox):
    settings = get_settings()
    assert settings.resolve_backend() == "fake"
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers():
    return {"X-API-Key": "dev-key-change-me"}


@pytest.fixture()
def admin_headers():
    return {"X-API-Key": "admin-key-change-me"}
