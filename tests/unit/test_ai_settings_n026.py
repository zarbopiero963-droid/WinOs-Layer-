"""N026 — AI secrets secure GUI / owner-only persist (H63-N026)."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from windows_os_api.apps.ai.provider import reset_ai_client
from windows_os_api.apps.ai.settings_store import (
    get_ai_settings,
    mask_api_key,
    reset_ai_settings,
    set_config_path_override,
    update_ai_settings,
)
from windows_os_api.core.runtime.config import get_settings


FAKE_KEY = "sk-test-n026-SECRET-do-not-leak-ABCDEF12"
FAKE_KEY_ROTATED = "sk-test-n026-ROTATED-new-key-XYZW9876"


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
    yield cfg
    set_config_path_override(None)
    reset_ai_settings()
    reset_ai_client()
    get_settings.cache_clear()


def test_unix_secure_write_mode_0600_and_dir_0700(ai_tmp: Path):
    update_ai_settings(provider="openai", api_key=FAKE_KEY, persist=True)
    assert ai_tmp.is_file()
    if sys.platform == "win32":
        pytest.skip("POSIX mode bits not authoritative on Windows")
    mode = ai_tmp.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
    # Other users must not have read bit (distinct-user intent H63-N026)
    assert not (mode & stat.S_IRGRP)
    assert not (mode & stat.S_IROTH)
    dir_mode = ai_tmp.parent.stat().st_mode & 0o777
    assert dir_mode == 0o700, f"expected dir 0700, got {oct(dir_mode)}"


def test_tmp_created_with_owner_only_mode_no_world_readable_window(ai_tmp: Path, monkeypatch):
    """Regression: old write_text+chmod left a brief world-readable tmp under permissive umask."""
    if sys.platform == "win32":
        pytest.skip("POSIX open mode / umask semantics")
    seen_modes: list[int] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        p = Path(src)
        if p.exists():
            seen_modes.append(p.stat().st_mode & 0o777)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    # permissive umask would make write_text create 0644; O_CREAT 0600 must still be owner-only
    old_umask = os.umask(0o000)
    try:
        update_ai_settings(provider="openai", api_key=FAKE_KEY, persist=True)
    finally:
        os.umask(old_umask)
    assert seen_modes, "expected to observe tmp mode before replace"
    assert all(m == 0o600 for m in seen_modes), seen_modes
    assert all(not (m & 0o044) for m in seen_modes)


def test_corrupt_json_fail_closed_no_leak(ai_tmp: Path):
    ai_tmp.write_text('{"api_key": "' + FAKE_KEY + '", NOT_JSON', encoding="utf-8")
    s = get_ai_settings(reload=True)
    # fail-closed → env base (local, empty key); never raise with file contents
    assert s.provider == "local"
    assert s.api_key_set() is False
    blob = json.dumps(s.public_dict()) + json.dumps(s.audit_detail())
    assert FAKE_KEY not in blob
    assert "NOT_JSON" not in blob


def test_non_dict_json_fail_closed(ai_tmp: Path):
    ai_tmp.write_text(json.dumps([FAKE_KEY, {"api_key": FAKE_KEY}]), encoding="utf-8")
    s = get_ai_settings(reload=True)
    assert s.api_key_set() is False
    assert FAKE_KEY not in json.dumps(s.public_dict())


def test_write_failure_no_half_applied_no_secret_in_error(ai_tmp: Path, monkeypatch):
    update_ai_settings(provider="openai", api_key=FAKE_KEY, persist=True)
    before = get_ai_settings().api_key
    assert before == FAKE_KEY

    def boom(*_a, **_k):
        raise OSError("simulated disk full")

    monkeypatch.setattr(
        "windows_os_api.apps.ai.settings_store.os.open",
        boom,
    )
    with pytest.raises(OSError) as ei:
        update_ai_settings(api_key=FAKE_KEY_ROTATED, persist=True)
    msg = str(ei.value)
    assert FAKE_KEY not in msg
    assert FAKE_KEY_ROTATED not in msg
    assert FAKE_KEY_ROTATED not in repr(ei.value)
    assert "failed to persist" in msg.lower()
    # chained cause may exist but HTTP/API layers must not surface it
    # runtime must keep previous durable key
    assert get_ai_settings().api_key == FAKE_KEY
    assert get_ai_settings().api_key != FAKE_KEY_ROTATED
    # no leftover tmp with new secret
    tmp = ai_tmp.with_suffix(ai_tmp.suffix + ".tmp")
    assert not tmp.exists()


def test_reload_preserves_valid_choice(ai_tmp: Path):
    update_ai_settings(
        provider="anthropic",
        api_key=FAKE_KEY,
        model="claude-3-5-haiku-20241022",
        persist=True,
    )
    reset_ai_settings()
    s = get_ai_settings(reload=True)
    assert s.provider == "anthropic"
    assert s.api_key == FAKE_KEY
    assert s.effective_model() == "claude-3-5-haiku-20241022"
    pub = s.public_dict()
    assert pub["api_key_set"] is True
    assert FAKE_KEY not in json.dumps(pub)
    assert pub["api_key_preview"] == mask_api_key(FAKE_KEY)


def test_rotation_public_dict_never_returns_old_or_new_raw(ai_tmp: Path):
    update_ai_settings(provider="openai", api_key=FAKE_KEY, persist=True)
    rotated = update_ai_settings(api_key=FAKE_KEY_ROTATED, persist=True)
    blob = json.dumps(rotated.public_dict()) + json.dumps(rotated.audit_detail())
    assert FAKE_KEY not in blob
    assert FAKE_KEY_ROTATED not in blob
    assert rotated.api_key == FAKE_KEY_ROTATED  # in-memory only
    assert rotated.public_dict()["api_key_preview"].endswith(FAKE_KEY_ROTATED[-4:])
    raw = json.loads(ai_tmp.read_text(encoding="utf-8"))
    assert raw["api_key"] == FAKE_KEY_ROTATED


def test_clear_key_rotation(ai_tmp: Path):
    update_ai_settings(provider="openai", api_key=FAKE_KEY, persist=True)
    cleared = update_ai_settings(api_key="", persist=True)
    assert cleared.api_key_set() is False
    assert cleared.public_dict()["api_key_preview"] is None
    assert FAKE_KEY not in json.dumps(cleared.public_dict())
    raw = json.loads(ai_tmp.read_text(encoding="utf-8"))
    assert raw.get("api_key") == ""


def test_public_dict_and_audit_never_contain_raw_key(ai_tmp: Path):
    s = update_ai_settings(provider="openai", api_key=FAKE_KEY, persist=True)
    for d in (s.public_dict(), s.audit_detail()):
        dumped = json.dumps(d)
        assert FAKE_KEY not in dumped
        assert "api_key" not in d or d.get("api_key") in (None, "")
