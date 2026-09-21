"""Pluggable AI HTTP clients — OpenAI / Anthropic / OpenRouter (+ local no-op).

N027: egress confined (URL/SSRF, no redirects, timeout/cancel, prompt redaction).
"""
from __future__ import annotations

import re
import threading
from typing import Any

import httpx

from windows_os_api.apps.ai.egress import (
    AIEgressError,
    redact_messages,
    safe_error_message,
    validate_ai_base_url,
    validate_request_url,
)
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

DEFAULT_TIMEOUT_S = 30.0


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
    """Chat completions client. Inject ``http_client`` for tests (httpx mock).

    Outbound calls are fail-closed: URL must pass egress validation, redirects
    are denied, timeouts do not claim spend, and prompt bodies are redacted.
    """

    def __init__(
        self,
        settings: AIRuntimeSettings | None = None,
        *,
        http_client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        resolve_dns: bool = True,
    ) -> None:
        self.settings = settings or get_ai_settings()
        self._timeout = timeout
        self._resolve_dns = resolve_dns
        self._cancelled = False
        self._owns_client = http_client is None
        if http_client is not None:
            self._client = http_client
        else:
            kwargs: dict[str, Any] = {
                "timeout": timeout,
                "follow_redirects": False,
                # N027: never honor HTTP(S)_PROXY / ALL_PROXY / trust_env DNS
                # side-channels that could bypass SSRF URL validation.
                "trust_env": False,
            }
            if transport is not None:
                kwargs["transport"] = transport
            self._client = httpx.Client(**kwargs)

    def close(self) -> None:
        self._cancelled = True
        if self._owns_client:
            self._client.close()

    def cancel(self) -> None:
        """Cancel in-flight use: teardown client; never claims spend."""
        self.close()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def remote_ready(self) -> bool:
        return self.settings.remote_ready()

    def _validated_base(self) -> str:
        base = self.settings.effective_base_url() or DEFAULT_BASE_URLS.get(
            self.settings.provider
        )
        if not base:
            raise AIEgressError("no base_url for provider", code="URL_MISSING")
        return validate_ai_base_url(base, resolve_dns=self._resolve_dns)

    def build_openai_request(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = 512,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Return (url, headers, json_body) for OpenAI-compatible chat/completions."""
        base = self._validated_base()
        url = validate_request_url(
            f"{base}/chat/completions", resolve_dns=self._resolve_dns
        )
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        if self.settings.provider == "openrouter":
            headers.setdefault("HTTP-Referer", "https://winos-layer.local")
            headers.setdefault("X-Title", "WinOs-Layer")
        safe_msgs = redact_messages(messages, api_key=self.settings.api_key)
        body = {
            "model": model or self.settings.effective_model(),
            "messages": safe_msgs,
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
        base = self._validated_base()
        url = validate_request_url(f"{base}/v1/messages", resolve_dns=self._resolve_dns)
        # Split system vs user/assistant
        system_parts: list[str] = []
        conv: list[dict[str, Any]] = []
        safe_msgs = redact_messages(messages, api_key=self.settings.api_key)
        for m in safe_msgs:
            role = m.get("role")
            content = m.get("content", "")
            if role == "system":
                system_parts.append(str(content))
            else:
                conv.append(
                    {
                        "role": role if role in ("user", "assistant") else "user",
                        "content": content,
                    }
                )
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

    def _post_json(
        self, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> httpx.Response:
        if self._cancelled:
            raise AIEgressError(
                "AI request cancelled", spent=False, code="CANCELLED"
            )
        # Re-validate at send time (TOCTOU / DNS rebind window)
        validate_request_url(url, resolve_dns=self._resolve_dns)
        try:
            resp = self._client.post(
                url,
                headers=headers,
                json=body,
                follow_redirects=False,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as e:
            raise AIEgressError(
                "AI provider request timed out",
                spent=False,
                code="TIMEOUT",
            ) from e
        except httpx.RequestError as e:
            raise AIEgressError(
                safe_error_message(e, api_key=self.settings.api_key),
                spent=False,
                code="REQUEST_FAILED",
            ) from e
        if self._cancelled:
            raise AIEgressError(
                "AI request cancelled", spent=False, code="CANCELLED"
            )
        if 300 <= resp.status_code < 400:
            loc = resp.headers.get("location") or resp.headers.get("Location")
            raise AIEgressError(
                f"AI egress redirect denied (HTTP {resp.status_code}"
                + (f" → {loc}" if loc else "")
                + ")",
                spent=False,
                code="REDIRECT_DENIED",
            )
        return resp

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = 512,
    ) -> str:
        """Call remote provider; raise if local / not ready / egress denied."""
        if self.settings.provider == "local" or not self.settings.api_key_set():
            raise RuntimeError("remote AI not configured (provider=local or no key)")
        if self._cancelled:
            raise AIEgressError("AI request cancelled", spent=False, code="CANCELLED")
        try:
            if self.settings.provider == "anthropic":
                url, headers, body = self.build_anthropic_request(
                    messages, model=model, max_tokens=max_tokens
                )
                resp = self._post_json(url, headers, body)
                resp.raise_for_status()
                data = resp.json()
                parts = data.get("content") or []
                texts = [
                    p.get("text", "")
                    for p in parts
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                return "\n".join(texts).strip()
            # openai + openrouter (OpenAI-compatible)
            url, headers, body = self.build_openai_request(
                messages, model=model, max_tokens=max_tokens
            )
            resp = self._post_json(url, headers, body)
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                return ""
            msg = choices[0].get("message") or {}
            return str(msg.get("content") or "").strip()
        except AIEgressError:
            raise
        except httpx.HTTPStatusError as e:
            raise AIEgressError(
                safe_error_message(e, api_key=self.settings.api_key),
                spent=False,
                code="HTTP_ERROR",
            ) from e

    def complete(self, prompt: str) -> str:
        """LLMProvider Protocol adapter. Fail-closed on egress/timeout (no spend claim)."""
        if not self.remote_ready():
            return ""
        if self._cancelled:
            return ""
        try:
            return self.chat([{"role": "user", "content": prompt}], max_tokens=256)
        except AIEgressError:
            return ""
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
            return {
                "ok": False,
                "error": safe_error_message(e, api_key=self.settings.api_key),
                "engine": f"ai:{self.settings.provider}",
            }
        return {"ok": True, "raw": raw, "engine": f"ai:{self.settings.provider}", "text": text}

    def test_connectivity(self, *, spend: bool = False) -> dict[str, Any]:
        """Dry connectivity / format test. Skips network for local.

        When ``spend=False`` (default): validate config + key format + egress URL only.
        When ``spend=True`` and remote ready: optional lightweight chat call.
        Timeout / cancel / egress deny → ok=False, spent=False.
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
            result.update(
                {
                    "ok": True,
                    "skipped_network": True,
                    "detail": "local OCR/reasoner — no remote call",
                }
            )
            return result
        ok_fmt, fmt_msg = validate_key_format(s.provider, s.api_key)
        result["format"] = fmt_msg
        if not s.api_key_set():
            result.update(
                {"ok": False, "skipped_network": True, "detail": "API key not set"}
            )
            return result
        if not ok_fmt:
            result.update({"ok": False, "skipped_network": True, "detail": fmt_msg})
            return result
        # Always validate egress URL (even dry-run)
        try:
            msgs = [{"role": "user", "content": "ping"}]
            if s.provider == "anthropic":
                url, headers, body = self.build_anthropic_request(msgs, max_tokens=1)
            else:
                url, headers, body = self.build_openai_request(msgs, max_tokens=1)
        except AIEgressError as e:
            result.update(
                {
                    "ok": False,
                    "skipped_network": True,
                    "spent": False,
                    "detail": str(e),
                    "egress_code": e.code,
                }
            )
            return result
        if not spend:
            result.update(
                {
                    "ok": True,
                    "skipped_network": True,
                    "detail": "format + request shape + egress URL validated (no network)",
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
            result.update(
                {
                    "ok": True,
                    "skipped_network": False,
                    "spent": True,
                    "detail": text[:80],
                }
            )
        except AIEgressError as e:
            result.update(
                {
                    "ok": False,
                    "skipped_network": False,
                    "spent": e.spent,
                    "detail": str(e),
                    "egress_code": e.code,
                }
            )
        except Exception as e:  # noqa: BLE001
            result.update(
                {
                    "ok": False,
                    "skipped_network": False,
                    "spent": False,
                    "detail": safe_error_message(e, api_key=s.api_key),
                }
            )
        return result


_client_lock = threading.RLock()
_client: AIProviderClient | None = None


def get_ai_client(
    *,
    http_client: httpx.Client | None = None,
    transport: httpx.BaseTransport | None = None,
    force_new: bool = False,
    resolve_dns: bool = True,
) -> AIProviderClient:
    global _client
    with _client_lock:
        if http_client is not None or transport is not None or force_new:
            return AIProviderClient(
                get_ai_settings(),
                http_client=http_client,
                transport=transport,
                resolve_dns=resolve_dns,
            )
        if _client is None:
            _client = AIProviderClient(get_ai_settings(), resolve_dns=resolve_dns)
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
