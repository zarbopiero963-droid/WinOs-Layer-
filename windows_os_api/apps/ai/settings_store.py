"""AI provider settings — env + secured user config file (owner-only ACL / chmod 600)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

AIProviderName = Literal["local", "openai", "anthropic", "openrouter"]

DEFAULT_MODELS: dict[str, str | None] = {
    "local": None,
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-20241022",
    "openrouter": "openai/gpt-4o-mini",
}

DEFAULT_BASE_URLS: dict[str, str | None] = {
    "local": None,
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "openrouter": "https://openrouter.ai/api/v1",
}

VALID_PROVIDERS = frozenset(DEFAULT_MODELS.keys())


def user_config_dir() -> Path:
    """Cross-platform user config dir for winos-api (Windows + Linux)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "winos-api"


def ai_settings_path() -> Path:
    return user_config_dir() / "ai_settings.json"


def mask_api_key(key: str | None) -> str | None:
    """Return masked preview like ``sk-…xxxx``; never the full secret."""
    if not key:
        return None
    k = key.strip()
    if not k:
        return None
    if len(k) <= 8:
        return "…" + k[-2:] if len(k) >= 2 else "…"
    # Prefer sk-…last4 when it looks like an OpenAI-style key
    prefix = k[:3] if k.startswith("sk-") or k.startswith("or-") else k[:2]
    return f"{prefix}…{k[-4:]}"


@dataclass
class AIRuntimeSettings:
    provider: AIProviderName = "local"
    api_key: str = ""
    model: str | None = None
    base_url: str | None = None

    def effective_model(self) -> str | None:
        if self.model:
            return self.model
        return DEFAULT_MODELS.get(self.provider)

    def effective_base_url(self) -> str | None:
        if self.base_url:
            return self.base_url.rstrip("/")
        return DEFAULT_BASE_URLS.get(self.provider)

    def api_key_set(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def remote_ready(self) -> bool:
        return self.provider != "local" and self.api_key_set()

    def public_dict(self) -> dict[str, Any]:
        """Safe for API responses — never includes the raw key."""
        key = self.api_key.strip() if self.api_key else ""
        return {
            "provider": self.provider,
            "model": self.effective_model(),
            "base_url": self.effective_base_url(),
            "api_key_set": bool(key),
            "api_key_preview": mask_api_key(key) if key else None,
            "remote_ready": self.remote_ready(),
        }

    def audit_detail(self) -> dict[str, Any]:
        """Safe for audit logs — last4 / flags only."""
        key = self.api_key.strip() if self.api_key else ""
        return {
            "provider": self.provider,
            "model": self.effective_model(),
            "base_url": self.effective_base_url(),
            "api_key_set": bool(key),
            "api_key_last4": key[-4:] if len(key) >= 4 else (key or None),
        }


_lock = threading.RLock()
_runtime: AIRuntimeSettings | None = None
# Tests can override config path
_config_path_override: Path | None = None


def set_config_path_override(path: Path | None) -> None:
    global _config_path_override
    _config_path_override = path


def _resolved_path() -> Path:
    return _config_path_override or ai_settings_path()


def _from_env() -> AIRuntimeSettings:
    from windows_os_api.core.runtime.config import get_settings

    s = get_settings()
    provider = (s.ai_provider or "local").lower().strip()
    if provider not in VALID_PROVIDERS:
        provider = "local"
    return AIRuntimeSettings(
        provider=provider,  # type: ignore[arg-type]
        api_key=(s.ai_api_key or "").strip(),
        model=s.ai_model or None,
        base_url=(s.ai_base_url or None),
    )


def _load_file(path: Path) -> dict[str, Any]:
    """Fail-closed: corrupt / non-dict / unreadable → ``{}`` (never echo contents)."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _merge(base: AIRuntimeSettings, file_data: dict[str, Any]) -> AIRuntimeSettings:
    provider = str(file_data.get("provider") or base.provider).lower().strip()
    if provider not in VALID_PROVIDERS:
        provider = base.provider
    api_key = file_data.get("api_key")
    if api_key is None:
        api_key = base.api_key
    model = file_data.get("model", base.model)
    if model == "":
        model = None
    base_url = file_data.get("base_url", base.base_url)
    if base_url == "":
        base_url = None
    return AIRuntimeSettings(
        provider=provider,  # type: ignore[arg-type]
        api_key=str(api_key or "").strip(),
        model=str(model) if model else None,
        base_url=str(base_url).rstrip("/") if base_url else None,
    )


def _windows_restrict_acl(path: Path) -> bool:
    """Best-effort owner-only DACL on Windows. Returns True if a restrictor ran.

    Prefer ``win32security`` when installed (optional ``[windows]`` extra); else
    ``icacls``. Never raises for missing APIs — callers treat False as honesty
    that ACL restriction was unavailable (POSIX chmod path still applies on Unix).
    """
    if sys.platform != "win32":
        return False
    p = str(path)
    # 1) pywin32 when present
    try:
        import win32security  # type: ignore[import-untyped]
        import ntsecuritycon as con  # type: ignore[import-untyped]

        user, _domain, _typ = win32security.LookupAccountName(None, os.getlogin())
        sd = win32security.SECURITY_DESCRIPTOR()
        sd.Initialize()
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, con.FILE_ALL_ACCESS, user)
        sd.SetSecurityDescriptorDacl(1, dacl, 0)
        win32security.SetFileSecurity(p, win32security.DACL_SECURITY_INFORMATION, sd)
        return True
    except Exception:
        pass
    # 2) icacls fallback (built-in on modern Windows)
    try:
        user = os.environ.get("USERNAME") or os.getlogin()
        # inheritance:r then grant:r current user full — replaces inherited ACEs
        r1 = subprocess.run(
            ["icacls", p, "/inheritance:r"],
            check=False,
            capture_output=True,
            timeout=30,
            text=True,
        )
        r2 = subprocess.run(
            ["icacls", p, "/grant:r", f"{user}:(F)"],
            check=False,
            capture_output=True,
            timeout=30,
            text=True,
        )
        return r1.returncode == 0 and r2.returncode == 0
    except Exception:
        return False


def _chmod_owner_only(path: Path, *, directory: bool = False) -> None:
    if sys.platform == "win32":
        _windows_restrict_acl(path)
        return
    mode = 0o700 if directory else 0o600
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _secure_write(path: Path, data: dict[str, Any]) -> None:
    """Atomic owner-only write. Never leaves a world-readable tmp under permissive umask.

    On Unix: create tmp with ``os.open(..., 0o600)``, fsync, ``os.replace``, chmod
    file ``0o600`` and parent dir ``0o700`` when creatable.
    On Windows: same atomic replace + best-effort owner ACL (pywin32 / icacls).
    On any failure: unlink ``.tmp`` if present and raise ``OSError`` whose message
    does **not** include the payload/secret.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod_owner_only(path.parent, directory=True)

    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd: int | None = None
    try:
        # mode 0o600 is masked by umask on Unix → still owner-only; Windows ignores mode bits
        fd = os.open(str(tmp), flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fd = None  # transferred to fh
            fh.write(payload)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        _chmod_owner_only(tmp, directory=False)
        os.replace(str(tmp), str(path))
        _chmod_owner_only(path, directory=False)
    except Exception as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        # Do not include path contents / payload / secret in the message
        raise OSError(f"failed to persist AI settings to {path.name}") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def get_ai_settings(*, reload: bool = False) -> AIRuntimeSettings:
    global _runtime
    with _lock:
        if _runtime is not None and not reload:
            return _runtime
        base = _from_env()
        file_data = _load_file(_resolved_path())
        _runtime = _merge(base, file_data) if file_data else base
        return _runtime


def update_ai_settings(
    *,
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    clear_key: bool = False,
    persist: bool = True,
) -> AIRuntimeSettings:
    """Update runtime settings. Empty ``api_key`` string clears the key.

    When ``persist=True``, the runtime cache is updated **only after** a durable
    secure write succeeds — a failed persist leaves the previous runtime key
    unchanged and never echoes secrets in the raised error.
    """
    global _runtime
    with _lock:
        current = get_ai_settings()
        new_provider = (provider or current.provider).lower().strip()
        if new_provider not in VALID_PROVIDERS:
            raise ValueError(f"invalid provider: {provider!r}")
        if clear_key or (api_key is not None and api_key.strip() == ""):
            new_key = ""
        elif api_key is None:
            new_key = current.api_key
        else:
            new_key = api_key.strip()
        new_model = current.model if model is None else (model.strip() or None)
        new_base = current.base_url if base_url is None else (base_url.strip().rstrip("/") or None)
        updated = AIRuntimeSettings(
            provider=new_provider,  # type: ignore[arg-type]
            api_key=new_key,
            model=new_model,
            base_url=new_base,
        )
        if persist:
            _secure_write(
                _resolved_path(),
                {
                    "provider": updated.provider,
                    "api_key": updated.api_key,
                    "model": updated.model,
                    "base_url": updated.base_url,
                },
            )
        _runtime = updated
        # Invalidate cached HTTP client
        from windows_os_api.apps.ai.provider import reset_ai_client

        reset_ai_client()
        return updated


def reset_ai_settings() -> None:
    global _runtime
    with _lock:
        _runtime = None
        from windows_os_api.apps.ai.provider import reset_ai_client

        reset_ai_client()
