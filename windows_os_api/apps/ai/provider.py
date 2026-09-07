"""Pluggable AI HTTP clients — OpenAI / Anthropic / OpenRouter (+ local no-op)."""
from __future__ import annotations

import re
import threading
from typing import Any

import httpx

from windows_os_api.apps.ai.settings_store import (
    AIRuntimeSettings,
    DEFAULT_BASE_URLS,
    get_ai_settings,
)

# Key format heuristics (validate without spending)
_KEY_PATTERNS: dict[str, re.Pattern[str]] = {
    "openai": re.compile(r"^sk-[A-Za-z0-9_\-]{10,}$"),
    "anthropic": re.compile(r"^sk-ant-[A-Za-z0-9_\-]{10,}$"),
    "openrouter": re.compile(r"^(sk-|sk-or-|or-)[A-Za-z0-9_\-]{10,}$"),
}


def validate_key_format(provider: str, api_key: str) -> tuple[bool, str]:
    """Return (ok, message). Local always ok; empty key fails for remote."""
    if provider == "local":
        return True, "local provider — no API key required"
    key = (api_key or "").strip()
    if not key:
        return False, "API key not set"
    if len(key) < 12:
        return False, "API key too short"
    pat = _KEY_PATTERNS.get(provider)
    if pat and not pat.match(key):
        # Soft warning — still allow non-matching keys (enterprise / custom)
        return True, f"key format unusual for {provider} (accepted)"
    return True, "key format ok"


class AIProviderClient:
    """Chat completions client. Inject ``http_client`` for tests (httpx mock)."""

    def __init__(
        self,
        settings: AIRuntimeSettings | None = None,
        *,
        http_client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.settings = settings or get_ai_settings()
        self._owns_client = http_client is None
        if http_client is not None:
            self._client = http_client
        else:
            kwargs: dict[str, Any] = {"timeout": timeout}
            if transport is not None:
                kwargs["transport"] = transport
            self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def remote_ready(self) -> bool:
        return self.settings.remote_ready()

    def build_openai_request(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = 512,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Return (url, headers, json_body) for OpenAI-compatible chat/completions."""
        base = self.settings.effective_base_url() or DEFAULT_BASE_URLS["openai"]
        url = f"{base.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        if self.settings.provider == "openrouter":
            headers.setdefault("HTTP-Referer", "https://winos-layer.local")
            headers.setdefault("X-Title", "WinOs-Layer")
        body = {
            "model": model or self.settings.effective_model(),
            "messages": messages,
            "max_tokens": max_tokens,
        }
        return url, headers, body

    def build_anthropic_request(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = 512,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Return (url, headers, json_body) for Anthropic Messages API."""
        base = self.settings.effective_base_url() or DEFAULT_BASE_URLS["anthropic"]
        url = f"{base.rstrip('/')}/v1/messages"
        # Split system vs user/assistant
        system_parts: list[str] = []
        conv: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "system":
                system_parts.append(str(content))
            else:
                conv.append({"role": role if role in ("user", "assistant") else "user", "content": content})
        headers = {
            "x-api-key": self.settings.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": model or self.settings.effective_model(),
            "messages": conv or [{"role": "user", "content": ""}],
            "max_tokens": max_tokens,
        }
        if system_parts:
            body["system"] = "\n".join(system_parts)
        return url, headers, body

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = 512,
    ) -> str:
        """Call remote provider; raise if local / not ready."""
        if self.settings.provider == "local" or not self.settings.api_key_set():
            raise RuntimeError("remote AI not configured (provider=local or no key)")
        if self.settings.provider == "anthropic":
            url, headers, body = self.build_anthropic_request(
                messages, model=model, max_tokens=max_tokens
            )
            resp = self._client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            parts = data.get("content") or []
            texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"]
            return "\n".join(texts).strip()
        # openai + openrouter (OpenAI-compatible)
        url, headers, body = self.build_openai_request(
            messages, model=model, max_tokens=max_tokens
        )
        resp = self._client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        return str(msg.get("content") or "").strip()

    def complete(self, prompt: str) -> str:
        """LLMProvider Protocol adapter."""
        if not self.remote_ready():
            return ""
        try:
            return self.chat([{"role": "user", "content": prompt}], max_tokens=256)
        except Exception:  # noqa: BLE001
            return ""

    def vision_locate_hint(
        self,
        text: str,
        *,
        image_b64: str | None = None,
        image_media_type: str = "image/png",
    ) -> dict[str, Any] | None:
        """Optional AI hint for vision find. Returns None to fall back to local OCR."""
        if not self.remote_ready():
            return None
        prompt = (
            f'Locate the on-screen text "{text}". '
            "Reply with a single JSON object: "
            '{"found": bool, "left": int, "top": int, "width": int, "height": int, '
            '"confidence": float}. If unsure, found=false.'
        )
        messages: list[dict[str, Any]]
        if image_b64 and self.settings.provider in ("openai", "openrouter"):
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image_media_type};base64,{image_b64}",
                            },
                        },
                    ],
                }
            ]
        elif image_b64 and self.settings.provider == "anthropic":
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": image_media_type,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ]
        else:
            messages = [{"role": "user", "content": prompt}]
        try:
            raw = self.chat(messages, max_tokens=200)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "engine": f"ai:{self.settings.provider}"}
        return {"ok": True, "raw": raw, "engine": f"ai:{self.settings.provider}", "text": text}

    def test_connectivity(self, *, spend: bool = False) -> dict[str, Any]:
        """Dry connectivity / format test. Skips network for local.

        When ``spend=False`` (default): validate config + key format only.
        When ``spend=True`` and remote ready: optional lightweight chat call.
        """
        s = self.settings
        result: dict[str, Any] = {
            "provider": s.provider,
            "model": s.effective_model(),
            "base_url": s.effective_base_url(),
            "api_key_set": s.api_key_set(),
            "spent": False,
        }
        if s.provider == "local":
            result.update({"ok": True, "skipped_network": True, "detail": "local OCR/reasoner — no remote call"})
            return result
        ok_fmt, fmt_msg = validate_key_format(s.provider, s.api_key)
        result["format"] = fmt_msg
        if not s.api_key_set():
            result.update({"ok": False, "skipped_network": True, "detail": "API key not set"})
            return result
        if not ok_fmt:
            result.update({"ok": False, "skipped_network": True, "detail": fmt_msg})
            return result
        if not spend:
            # Build request to prove wiring without sending
            msgs = [{"role": "user", "content": "ping"}]
            if s.provider == "anthropic":
                url, headers, body = self.build_anthropic_request(msgs, max_tokens=1)
            else:
                url, headers, body = self.build_openai_request(msgs, max_tokens=1)
            result.update(
                {
                    "ok": True,
                    "skipped_network": True,
                    "detail": "format + request shape validated (no network)",
                    "request_url": url,
                    "request_model": body.get("model"),
                    # Never echo Authorization / x-api-key values
                    "auth_header_present": (
                        "Authorization" in headers or "x-api-key" in headers
                    ),
                }
            )
            return result
        try:
            text = self.chat([{"role": "user", "content": "Reply with OK"}], max_tokens=8)
            result.update({"ok": True, "skipped_network": False, "spent": True, "detail": text[:80]})
        except Exception as e:  # noqa: BLE001
            result.update({"ok": False, "skipped_network": False, "spent": True, "detail": str(e)})
        return result


_client_lock = threading.RLock()
_client: AIProviderClient | None = None


def get_ai_client(
    *,
    http_client: httpx.Client | None = None,
    transport: httpx.BaseTransport | None = None,
    force_new: bool = False,
) -> AIProviderClient:
    global _client
    with _client_lock:
        if http_client is not None or transport is not None or force_new:
            return AIProviderClient(
                get_ai_settings(),
                http_client=http_client,
                transport=transport,
            )
        if _client is None:
            _client = AIProviderClient(get_ai_settings())
        return _client


def reset_ai_client() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:  # noqa: BLE001
                pass
        _client = None


def sync_llm_bridge() -> None:
    """Wire remote client into offline reasoner when ready; else NullLLM."""
    from windows_os_api.apps.reasoning.offline import NullLLM, set_llm

    settings = get_ai_settings()
    if settings.remote_ready():
        set_llm(get_ai_client())
    else:
        set_llm(NullLLM())
