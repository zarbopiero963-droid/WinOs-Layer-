"""Unit tests for optional AI provider settings + HTTP clients."""
from __future__ import annotations

import json

import httpx
import pytest

from windows_os_api.apps.ai.provider import (
    AIProviderClient,
    get_ai_client,
    reset_ai_client,
    validate_key_format,
)
from windows_os_api.apps.ai.settings_store import (
    AIRuntimeSettings,
    get_ai_settings,
    mask_api_key,
    reset_ai_settings,
    set_config_path_override,
    update_ai_settings,
)
from windows_os_api.apps.reasoning.offline import NullLLM, reason, set_llm
from windows_os_api.apps.vision.ocr import find_text_on_image, render_text_fixture
from windows_os_api.core.runtime.config import get_settings


@pytest.fixture()
def ai_tmp(tmp_path, monkeypatch):
    cfg = tmp_path / "ai_settings.json"
    monkeypatch.setenv("WINOS_AI_PROVIDER", "local")
    monkeypatch.setenv("WINOS_AI_API_KEY", "")
    monkeypatch.delenv("WINOS_AI_MODEL", raising=False)
    monkeypatch.delenv("WINOS_AI_BASE_URL", raising=False)
    get_settings.cache_clear()
    set_config_path_override(cfg)
    reset_ai_settings()
    reset_ai_client()
    set_llm(NullLLM())
    yield cfg
    set_config_path_override(None)
    reset_ai_settings()
    reset_ai_client()
    set_llm(NullLLM())
    get_settings.cache_clear()


def test_mask_api_key():
    assert mask_api_key(None) is None
    assert mask_api_key("") is None
    assert mask_api_key("sk-abcdefghijklmnopqrstuvwxyz") == "sk-…wxyz"
    preview = mask_api_key("sk-proj-ABCDEFGHijkl")
    assert preview is not None
    assert "ABCDEFGH" not in preview
    assert preview.endswith("ijkl")
    assert "…" in preview


def test_settings_default_local(ai_tmp):
    s = get_ai_settings(reload=True)
    assert s.provider == "local"
    assert s.api_key_set() is False
    assert s.remote_ready() is False
    pub = s.public_dict()
    assert pub["api_key_set"] is False
    assert pub["api_key_preview"] is None
    assert "api_key" not in pub


def test_update_persist_chmod_and_clear(ai_tmp):
    updated = update_ai_settings(
        provider="openai",
        api_key="sk-testkey1234567890abcd",
        model="gpt-4o-mini",
        persist=True,
    )
    assert updated.remote_ready() is True
    assert ai_tmp.is_file()
    raw = json.loads(ai_tmp.read_text())
    assert raw["api_key"] == "sk-testkey1234567890abcd"
    # Unix: expect 0o600
    import sys

    if sys.platform != "win32":
        mode = ai_tmp.stat().st_mode & 0o777
        assert mode == 0o600
    pub = updated.public_dict()
    assert pub["api_key_set"] is True
    assert pub["api_key_preview"] == "sk-…abcd"
    assert "sk-testkey1234567890abcd" not in json.dumps(pub)

    cleared = update_ai_settings(api_key="", persist=True)
    assert cleared.api_key_set() is False
    assert cleared.remote_ready() is False


def test_fallback_to_local_without_key(ai_tmp, monkeypatch):
    monkeypatch.setenv("WINOS_AI_PROVIDER", "openai")
    monkeypatch.setenv("WINOS_AI_API_KEY", "")
    get_settings.cache_clear()
    reset_ai_settings()
    s = get_ai_settings(reload=True)
    assert s.provider == "openai"
    assert s.remote_ready() is False
    client = AIProviderClient(s)
    result = client.test_connectivity(spend=False)
    assert result["ok"] is False
    assert result["skipped_network"] is True
    # Vision still works locally
    img = render_text_fixture("HelloAI")
    boxes = find_text_on_image(img, "HelloAI", prefer_ai=True)
    assert boxes
    assert boxes[0]["engine"] in ("template", "tesseract")


def test_openai_compatible_request_mocked(ai_tmp):
    settings = AIRuntimeSettings(
        provider="openai",
        api_key="sk-testkey1234567890abcd",
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
    )
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "pong"}}],
            },
        )

    transport = httpx.MockTransport(handler)
    client = AIProviderClient(settings, transport=transport)
    url, headers, body = client.build_openai_request(
        [{"role": "user", "content": "ping"}], max_tokens=16
    )
    assert url.endswith("/chat/completions")
    assert headers["Authorization"].startswith("Bearer sk-")
    assert body["model"] == "gpt-4o-mini"
    assert body["messages"][0]["content"] == "ping"
    # Full key must never appear in public dict / audit
    assert "sk-testkey1234567890abcd" not in json.dumps(settings.public_dict())
    assert "sk-testkey1234567890abcd" not in json.dumps(settings.audit_detail())

    text = client.chat([{"role": "user", "content": "ping"}])
    assert text == "pong"
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["model"] == "gpt-4o-mini"


def test_openrouter_and_anthropic_request_shapes(ai_tmp):
    or_s = AIRuntimeSettings(
        provider="openrouter",
        api_key="sk-or-v1-abcdefghijklmnopqrstuvwxyz",
        model="openai/gpt-4o-mini",
    )
    or_c = AIProviderClient(or_s)
    url, headers, body = or_c.build_openai_request([{"role": "user", "content": "hi"}])
    assert "openrouter.ai" in url
    assert headers["Authorization"].startswith("Bearer ")
    assert body["model"] == "openai/gpt-4o-mini"

    ant = AIRuntimeSettings(
        provider="anthropic",
        api_key="sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        model="claude-3-5-haiku-20241022",
    )
    ant_c = AIProviderClient(ant)
    url, headers, body = ant_c.build_anthropic_request(
        [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert url.endswith("/v1/messages")
    assert headers["x-api-key"].startswith("sk-ant-")
    assert "Authorization" not in headers
    assert body["system"] == "be brief"
    assert body["messages"][0]["role"] == "user"


def test_local_test_skips_network(ai_tmp):
    client = get_ai_client(force_new=True)
    r = client.test_connectivity(spend=False)
    assert r["ok"] is True
    assert r["skipped_network"] is True
    assert r["provider"] == "local"


def test_provider_selection_from_update(ai_tmp):
    update_ai_settings(provider="openrouter", api_key="sk-or-v1-abcdefghijklmnop", persist=True)
    s = get_ai_settings()
    assert s.provider == "openrouter"
    assert s.effective_base_url() and "openrouter" in s.effective_base_url()
    ok, _ = validate_key_format("openrouter", s.api_key)
    assert ok is True


def test_reason_falls_back_without_key(ai_tmp):
    set_llm(NullLLM())
    r = reason("save")
    assert r["engine"] == "deterministic-offline"
    assert r["llm_hint"] is None
