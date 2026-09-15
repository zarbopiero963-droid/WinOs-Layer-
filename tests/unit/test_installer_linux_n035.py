"""H63-N035 — Installer Linux: LinuxBackend default, --api-key-file, weak-key reject."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LINUX = ROOT / "installer" / "linux"


def _install_sh() -> str:
    return (LINUX / "install.sh").read_text(encoding="utf-8")


def _uninstall_sh() -> str:
    return (LINUX / "uninstall.sh").read_text(encoding="utf-8")


def _unit() -> str:
    return (LINUX / "winos-api.service").read_text(encoding="utf-8")


def _bi_mod():
    path = ROOT / "scripts" / "build_installer.py"
    spec = importlib.util.spec_from_file_location("build_installer_n035", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_h63_n035_install_sh_no_fakebackend_in_messaging():
    sh = _install_sh()
    assert "FakeBackend" not in sh
    assert "WINOS_BACKEND=fake" not in sh
    # Final start path uses auto + api-key-file
    assert "WINOS_BACKEND=auto" in sh
    assert "--api-key-file" in sh
    assert "WINOS_API_KEYS=" not in sh  # no secret in final MSG / env print


def test_h63_n035_install_sh_user_vs_system_dest():
    sh = _install_sh()
    assert '/.local/opt/winos-api' in sh or "${HOME}/.local/opt/winos-api" in sh
    assert 'DEST="/opt/winos-api"' in sh or 'DEST=/opt/winos-api' in sh
    assert "--user" in sh
    assert "~/.local/opt/winos-api" in sh or "${HOME}/.local/opt/winos-api" in sh
    assert "/opt/winos-api" in sh


def test_h63_n035_install_sh_weak_denylist_present():
    sh = _install_sh()
    for weak in (
        "dev",
        "admin",
        "test",
        "password",
        "secret",
        "changeme",
        "dev-key-change-me",
        "winos-dev",
        "winos-admin",
    ):
        assert weak in sh, weak
    assert "is_weak_api_key" in sh or "denylist" in sh.lower()


def test_h63_n035_install_sh_header_linuxbackend():
    sh = _install_sh()
    head = "\n".join(sh.splitlines()[:12])
    assert "LinuxBackend" in head or "real OS" in head
    assert "FakeBackend" not in head


def test_h63_n035_unit_api_key_file_backend_auto_localhost():
    unit = _unit()
    assert "--api-key-file api_key.txt" in unit
    assert "WINOS_BACKEND=auto" in unit
    assert "127.0.0.1" in unit
    desc = next(ln for ln in unit.splitlines() if ln.startswith("Description="))
    assert "LinuxBackend" in desc
    assert "FakeBackend" not in desc
    exec_line = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
    assert "FakeBackend" not in exec_line


def test_h63_n035_uninstall_sh_no_fakebackend_product_claim():
    sh = _uninstall_sh()
    head = "\n".join(sh.splitlines()[:10])
    assert "FakeBackend" not in head
    assert "LinuxBackend" in head or "real OS" in head


def test_h63_n035_validate_returns_new_checks_true():
    result = _bi_mod().validate(verbose=False)
    assert result["ok"] is True, result.get("errors")
    for key in (
        "linux_unit_api_key_file",
        "linux_unit_backend_auto",
        "linux_install_no_fake_default",
        "linux_install_no_secret_print",
        "linux_install_api_key_file",
        "linux_install_backend_auto",
        "linux_unit_localhost",
    ):
        assert result["checks"].get(key) is True, (key, result["checks"])


def test_h63_n035_load_api_key_file_rejects_weak(tmp_path):
    from windows_os_api.cli.main import load_api_key_file

    weak = tmp_path / "weak.txt"
    weak.write_text("dev-key-change-me\n", encoding="utf-8")
    with pytest.raises(ValueError, match="denylist|weak"):
        load_api_key_file(weak)

    empty = tmp_path / "empty.txt"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_api_key_file(empty)

    strong = tmp_path / "strong.txt"
    strong.write_text("a" * 48 + "\n", encoding="utf-8")
    assert load_api_key_file(strong) == "a" * 48


def test_h63_n035_serve_rejects_weak_key_file(tmp_path, monkeypatch):
    from windows_os_api.cli import main as cli_main

    weak = tmp_path / "api_key.txt"
    weak.write_text("winos-dev\n", encoding="utf-8")
    # Avoid actually starting uvicorn
    monkeypatch.setattr(
        "uvicorn.run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("uvicorn must not start")),
    )
    rc = cli_main.main(
        [
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
            "--api-key-file",
            str(weak),
        ]
    )
    assert rc == 2


def test_h63_n035_serve_loads_strong_key_into_env(tmp_path, monkeypatch):
    from windows_os_api.cli import main as cli_main
    from windows_os_api.core.runtime.config import get_settings

    key_file = tmp_path / "api_key.txt"
    secret = "n035-strong-key-" + ("x" * 32)
    key_file.write_text(secret + "\n", encoding="utf-8")

    seen: dict = {}

    def fake_run(*_a, **_k):
        seen["keys"] = __import__("os").environ.get("WINOS_API_KEYS")
        seen["auth"] = __import__("os").environ.get("WINOS_REQUIRE_AUTH")

    monkeypatch.setattr("uvicorn.run", fake_run)
    # Track + restore conftest auth env so later tests keep X-API-Key: dev-key-change-me.
    monkeypatch.setenv("WINOS_API_KEYS", '["dev-key-change-me"]')
    monkeypatch.setenv("WINOS_REQUIRE_AUTH", "false")
    rc = cli_main.main(
        ["serve", "--host", "127.0.0.1", "--port", "8765", "--api-key-file", str(key_file)]
    )
    assert rc == 0
    assert json.loads(seen["keys"]) == [secret]
    assert seen["auth"] == "true"
    get_settings.cache_clear()


def test_h63_n035_package_linux_docstring_linuxbackend():
    bi = _bi_mod()
    doc = bi.package_linux.__doc__ or ""
    assert "LinuxBackend" in doc or "real OS" in doc
    assert "FakeBackend-capable" not in doc
