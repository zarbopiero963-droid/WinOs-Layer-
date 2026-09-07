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

    # Security
    api_keys: list[str] = Field(default_factory=lambda: ["dev-key-change-me"])
    require_auth: bool = True
    admin_api_keys: list[str] = Field(default_factory=lambda: ["admin-key-change-me"])
    rate_limit_per_minute: int = 120
    audit_log_path: str = "logs/audit.jsonl"
    sandbox_root: str = "sandbox"
    # Extra filesystem roots allowed by LinuxBackend (still blocks ..)
    fs_allow_paths: list[str] = Field(default_factory=list)

    # Backend selection: auto → windows on win32, linux on Linux, never fake unless forced
    backend: Literal["auto", "windows", "linux", "fake"] = "auto"
    # Only when true may factory fall back to FakeBackend if windows/linux unavailable
    allow_fake_fallback: bool = False
    # Opt-in for pkexec/sudo elevation (still requires ADMIN permission)
    allow_privileged: bool = False

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
