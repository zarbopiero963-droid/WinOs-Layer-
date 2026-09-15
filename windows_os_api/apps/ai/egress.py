"""AI egress confinement — URL/SSRF validation, redirect deny, prompt redaction (N027).

Untrusted UI / log / model text is data, never authorization. Secrets must not
leave the process in prompt bodies or error strings.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse

# Default provider hostnames (exact match, case-insensitive).
ALLOWED_DEFAULT_HOSTS = frozenset(
    {
        "api.openai.com",
        "api.anthropic.com",
        "openrouter.ai",
    }
)

# Metadata / cloud-config endpoints commonly abused for SSRF.
_BLOCKED_LITERAL_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
    }
)

# API-key-like tokens that must never ride inside prompt / message bodies.
_SECRET_BODY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(api[_-]?key|x-api-key|authorization|bearer|token)\s*[:=]\s*\S+"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bsk-or-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}\b"),
    re.compile(r"\bor-[A-Za-z0-9_\-]{10,}\b"),
)


class AIEgressError(Exception):
    """Fail-closed AI egress / untrusted-input violation.

    ``spent`` is False unless a remote call was known to have been charged.
    Timeout, cancel, SSRF block, and redirect deny never claim spend.
    """

    def __init__(self, message: str, *, spent: bool = False, code: str = "EGRESS_DENIED") -> None:
        super().__init__(message)
        self.spent = spent
        self.code = code


def _host_is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def is_blocked_ip(ip: str) -> bool:
    """True for private, loopback, link-local, multicast, reserved, unspecified."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return True
    if addr.is_multicast or addr.is_reserved or addr.is_unspecified:
        return True
    # Explicit metadata (IPv4 link-local is already covered; keep clarity).
    if str(addr) in ("169.254.169.254", "fd00:ec2::254"):
        return True
    return False


def _resolve_host_ips(hostname: str) -> list[str]:
    """Resolve A/AAAA; empty list on failure (caller fail-closes)."""
    ips: list[str] = []
    try:
        for family, _type, _proto, _canon, sockaddr in socket.getaddrinfo(
            hostname, None, type=socket.SOCK_STREAM
        ):
            if family == socket.AF_INET:
                ips.append(sockaddr[0])
            elif family == socket.AF_INET6:
                ips.append(sockaddr[0])
    except OSError:
        return []
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for ip in ips:
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def validate_ai_base_url(
    url: str | None,
    *,
    resolve_dns: bool = True,
    allow_custom_hosts: bool = True,
) -> str:
    """Validate an AI provider base URL. Returns normalised URL (no trailing slash).

    Rules:
    - https only
    - no credentials in URL (userinfo)
    - hostname required; no empty / relative
    - IP literals must not be private/link-local/metadata
    - default provider hosts always OK (still DNS-checked when resolve_dns)
    - optional custom host only if ``allow_custom_hosts`` and SSRF checks pass
    - blocked metadata hostnames denied
    """
    if url is None or not str(url).strip():
        raise AIEgressError("base_url is required for remote AI egress", code="URL_MISSING")

    raw = str(url).strip().rstrip("/")
    parsed = urlparse(raw)

    if parsed.scheme.lower() != "https":
        raise AIEgressError(
            f"AI egress requires https (got {parsed.scheme!r})",
            code="SCHEME_DENIED",
        )
    if parsed.username is not None or parsed.password is not None:
        raise AIEgressError("credentials in URL are forbidden", code="URL_CREDENTIALS")
    if not parsed.hostname:
        raise AIEgressError("AI egress URL missing hostname", code="HOST_MISSING")
    if parsed.port is not None and parsed.port not in (443,):
        # Allow only default HTTPS port for provider APIs (fail-closed).
        raise AIEgressError(
            f"AI egress port {parsed.port} not allowed (https/443 only)",
            code="PORT_DENIED",
        )

    host = parsed.hostname.lower().rstrip(".")
    if host in _BLOCKED_LITERAL_HOSTS:
        raise AIEgressError(f"host {host!r} is blocked", code="HOST_DENIED")

    is_default = host in ALLOWED_DEFAULT_HOSTS
    if not is_default and not allow_custom_hosts:
        raise AIEgressError(
            f"host {host!r} is not an allowed default provider",
            code="HOST_DENIED",
        )

    if _host_is_ip_literal(host):
        if is_blocked_ip(host):
            raise AIEgressError(
                "AI egress to private/link-local/metadata IP is forbidden",
                code="IP_DENIED",
            )
        # Public IP literal as custom base — allowed only if custom hosts OK
        if not allow_custom_hosts:
            raise AIEgressError("IP literal hosts are not default providers", code="HOST_DENIED")
    elif resolve_dns:
        ips = _resolve_host_ips(host)
        if not ips:
            raise AIEgressError(
                f"DNS resolution failed for {host!r}",
                code="DNS_FAILED",
            )
        for ip in ips:
            if is_blocked_ip(ip):
                raise AIEgressError(
                    "AI egress DNS resolved to private/link-local/metadata IP",
                    code="DNS_IP_DENIED",
                )

    # Rebuild without trailing slash / without fragment; keep path
    path = parsed.path.rstrip("/") if parsed.path else ""
    if path == "/":
        path = ""
    normalised = f"https://{host}{path}"
    if parsed.query:
        normalised = f"{normalised}?{parsed.query}"
    return normalised


def validate_request_url(url: str, *, resolve_dns: bool = True) -> str:
    """Validate a full request URL (base + path) before POST."""
    return validate_ai_base_url(url, resolve_dns=resolve_dns, allow_custom_hosts=True)


def redirect_target_allowed(location: str | None, *, resolve_dns: bool = True) -> bool:
    """Return True only if Location is a fully allowed https URL."""
    if not location or not str(location).strip():
        return False
    try:
        validate_ai_base_url(str(location).strip(), resolve_dns=resolve_dns)
        return True
    except AIEgressError:
        return False


def redact_secrets_in_text(text: str, extra_secrets: list[str] | None = None) -> str:
    """Strip API-key-like patterns from untrusted prompt / log text."""
    if not text:
        return text
    out = text
    for secret in extra_secrets or []:
        if secret and len(secret) >= 4 and secret in out:
            out = out.replace(secret, "[REDACTED]")
    for pat in _SECRET_BODY_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


def redact_messages(
    messages: list[dict[str, Any]],
    *,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Deep-copy messages with secret-like substrings removed from content."""
    extras = [api_key] if api_key else None
    cleaned: list[dict[str, Any]] = []
    for msg in messages:
        item = dict(msg)
        content = item.get("content")
        if isinstance(content, str):
            item["content"] = redact_secrets_in_text(content, extras)
        elif isinstance(content, list):
            new_parts: list[Any] = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    p = dict(part)
                    p["text"] = redact_secrets_in_text(p["text"], extras)
                    new_parts.append(p)
                else:
                    new_parts.append(part)
            item["content"] = new_parts
        cleaned.append(item)
    return cleaned


def safe_error_message(exc: BaseException, *, api_key: str | None = None) -> str:
    """Error string for logs/API — never echo the raw API key."""
    msg = str(exc)
    if api_key and api_key.strip() and api_key in msg:
        msg = msg.replace(api_key, "[REDACTED]")
    return redact_secrets_in_text(msg)


# Claims that prompt-injection may try to set on a plan — never authorization.
_INJECTION_AUTH_KEYS = frozenset(
    {
        "force_execute",
        "bypass_gate",
        "enable_tools",
        "skip_confirmation",
        "authorized",
        "permission_granted",
    }
)


def strip_injection_auth_claims(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy without untrusted authorization claim keys.

    Confidence is not permission; model/UI text must not grant tool access by
    smuggling force_execute / bypass_gate / enable_tools onto the plan dict.
    """
    return {k: v for k, v in plan.items() if k not in _INJECTION_AUTH_KEYS}
