"""Secure configuration — localhost by default, remote disabled."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WINOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Windows OS API Layer"
    api_version: str = "v1"
    host: str = "127.0.0.1"
    port: int = 8765
    remote_access_enabled: bool = False
    allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1", "localhost"])

    # Security — N011: release defaults carry NO baked-in keys.
    # Assign keys via WINOS_*_API_KEYS env (four roles). Explicit env may
    # still set documentation placeholders for local/CI; empty default =
    # release path rejects them (not present in any list).
    api_keys: list[str] = Field(default_factory=list)  # AUTOMATOR
    viewer_api_keys: list[str] = Field(default_factory=list)
    operator_api_keys: list[str] = Field(default_factory=list)
    admin_api_keys: list[str] = Field(default_factory=list)
    require_auth: bool = True
    rate_limit_per_minute: int = 120
    # N013 — request / concurrency / body / CORS confines
    max_body_bytes: int = 1_048_576  # 1 MiB default body cap
    max_concurrent_requests: int = 32
    # When remote_access_enabled: explicit Origin allowlist (never implicit "*").
    cors_allowed_origins: list[str] = Field(default_factory=list)
    audit_log_path: str = "logs/audit.jsonl"
    # N042 — integrity HMAC secret (override in production via WINOS_AUDIT_INTEGRITY_SECRET)
    audit_integrity_secret: str = "winos-audit-dev-secret"
    audit_max_bytes: int = 10_485_760  # 10 MiB rotation threshold
    audit_max_entries: int = 100_000
    audit_max_age_seconds: float | None = None  # optional age-based rotation
    sandbox_root: str = "sandbox"
    # Extra filesystem roots allowed by LinuxBackend (still blocks ..)
    fs_allow_paths: list[str] = Field(default_factory=list)

    # Backend selection: auto → windows on win32, linux on Linux, never fake unless forced
    backend: Literal["auto", "windows", "linux", "fake"] = "auto"
    # Only when true may factory fall back to FakeBackend if windows/linux unavailable
    allow_fake_fallback: bool = False
    # Opt-in for pkexec/sudo elevation (still requires ADMIN permission)
    allow_privileged: bool = False

    # Optional remote AI (OpenAI / Anthropic / OpenRouter). Default local = Pillow/tesseract.
    # Secrets: never log full WINOS_AI_API_KEY; persist under user config dir chmod 600.
    ai_provider: Literal["local", "openai", "anthropic", "openrouter"] = "local"
    ai_api_key: str = ""
    ai_model: str | None = None
    ai_base_url: str | None = None

    # Observability
    metrics_enabled: bool = True

    # Update
    update_channel: str = "stable"
    update_verify_signatures: bool = True

    @field_validator("host")
    @classmethod
    def enforce_localhost_when_remote_disabled(cls, v: str, info) -> str:
        return v

    def effective_host(self) -> str:
        if not self.remote_access_enabled:
            return "127.0.0.1"
        return self.host

    def resolve_backend(self) -> str:
        if self.backend != "auto":
            return self.backend
        import sys

        if sys.platform == "win32":
            return "windows"
        # Real Linux by default (FakeBackend only when WINOS_BACKEND=fake)
        return "linux"

    def ensure_dirs(self) -> None:
        Path(self.audit_log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.sandbox_root).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
