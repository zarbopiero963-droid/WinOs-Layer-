"""`GET /v1/registry` lettura fail-closed (D6 / N005): allowlist + denylist.

Contratto (#67 N005 / #64 D6): prefissi espliciti + denylist immutabile + filtro
valori; nessun accesso prima della policy (`service.read` -> `check_read`).
"""
from __future__ import annotations

import pytest

from windows_os_api.os.registry import service as registry_service
from windows_os_api.os.registry.allowlist import (
    DEFAULT_READ_PREFIXES,
    ENV_VAR_READ,
    REGISTRY_PATH_INVALID,
    REGISTRY_READ_FORBIDDEN,
    REGISTRY_READ_NOT_ALLOWED,
    REGISTRY_VALUE_FORBIDDEN,
    SECRET_VALUE_TERMS,
    allowed_read_prefixes,
    check_read,
)

pytestmark = pytest.mark.security

ALLOWED_TEMP = r"HKCU\Software\WinOsLayer\N005Probe"


class SpyBackend:
    def __init__(self, values: dict | None = None):
        self.calls: list[tuple] = []
        self._values = values or {"Theme": "dark"}

    def registry_read(self, path, name=None):
        self.calls.append((path, name))
        if name is None:
            return {"ok": True, "path": path, "values": dict(self._values)}
        if name not in self._values:
            return {"ok": False, "error": "value not found", "path": path, "name": name}
        return {"ok": True, "path": path, "name": name, "value": self._values[name]}


@pytest.fixture
def spy(monkeypatch):
    backend = SpyBackend()
    monkeypatch.setattr(registry_service, "get_backend", lambda: backend)
    return backend


def test_the_spy_is_actually_wired(spy):
    result = registry_service.read(ALLOWED_TEMP, "Theme")
    assert result["ok"] is True, result
    assert spy.calls == [(ALLOWED_TEMP, "Theme")]


@pytest.mark.parametrize(
    "path",
    [
        r"HKLM\SAM\SAM\Domains\Account\Users",
        r"HKLM\SECURITY\Policy\Secrets\DefaultPassword",
        r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa",
        r"HKLM\SYSTEM\ControlSet001\Control\Lsa",
        r"HKLM\SYSTEM\ControlSet002\Control\Lsa",
        r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
        r"HKU\S-1-5-21-1111111111-2222222222-3333333333-1001\Software\Contoso",
        r"HKLM\SAM",
        r"HKU",
    ],
)
def test_a_forbidden_area_is_refused_and_never_reaches_the_backend(spy, path):
    result = registry_service.read(path)
    assert result["ok"] is False, result
    assert result["denied"] is True, result
    assert result["code"] == REGISTRY_READ_FORBIDDEN, result
    assert spy.calls == []


@pytest.mark.parametrize(
    "path",
    [
        r"HKEY_LOCAL_MACHINE\SECURITY\Policy",
        r"HKEY_USERS\S-1-5-21-1-2-3-1001\Software",
        "hklm/security/policy",
        r"HKLM\\SECURITY\\Policy",
        "  HKLM\\SECURITY\\Policy  ",
        r"hkLm\SaM\Domains",
    ],
)
def test_the_denylist_is_not_dodged_by_how_the_path_is_written(spy, path):
    result = registry_service.read(path)
    assert result["ok"] is False, result
    assert result["code"] == REGISTRY_READ_FORBIDDEN, result
    assert spy.calls == []


def test_a_relative_segment_is_refused_not_resolved(spy):
    result = registry_service.read(r"HKCU\Software\..\..\SECURITY\Policy")
    assert result["ok"] is False, result
    assert result["code"] == REGISTRY_PATH_INVALID, result
    assert spy.calls == []


@pytest.mark.parametrize(
    "path",
    [
        r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion",
        r"HKLM\SOFTWARE\WinOsApi",
        r"HKCR\.txt",
        r"HKCR\txtfile",
        r"HKLM\SAMPLE\Things",
        r"HKCC\System\CurrentControlSet\Control",
        r"HKCU\Environment",
    ],
)
def test_paths_outside_read_allowlist_never_reach_the_backend(spy, path):
    result = registry_service.read(path)
    assert result["ok"] is False, result
    assert result["denied"] is True, result
    assert result["code"] == REGISTRY_READ_NOT_ALLOWED, result
    assert spy.calls == []


def test_default_read_prefixes_include_winoslayer_space():
    assert DEFAULT_READ_PREFIXES == ("HKCU\\SOFTWARE\\",)
    assert check_read(r"HKCU\Software\WinOsLayer\Probe") == r"HKCU\Software\WinOsLayer\Probe"


def test_allowlist_prefix_without_separator_does_not_swallow_sibling(monkeypatch, spy):
    monkeypatch.setenv(ENV_VAR_READ, r"HKCU\Tools")
    result = registry_service.read(r"HKCU\SoftwareAltro\X", "Theme")
    assert result["code"] == REGISTRY_READ_NOT_ALLOWED, result
    assert spy.calls == []


def test_read_allowlist_env_extends_and_reaches_backend(monkeypatch, spy):
    monkeypatch.setenv(ENV_VAR_READ, r"HKLM\SOFTWARE\WinOsApi\,HKCU\Tools\\")
    prefixes = allowed_read_prefixes()
    assert "HKLM\\SOFTWARE\\WINOSAPI\\" in prefixes
    assert "HKCU\\TOOLS\\" in prefixes
    result = registry_service.read(r"HKLM\SOFTWARE\WinOsApi\Settings", "Theme")
    assert result["ok"] is True, result
    assert spy.calls == [(r"HKLM\SOFTWARE\WinOsApi\Settings", "Theme")]


def test_denylist_not_bypassed_by_read_allowlist_env(monkeypatch, spy):
    monkeypatch.setenv(ENV_VAR_READ, r"HKLM\SYSTEM\,HKLM\SECURITY\,HKU\\")
    assert allowed_read_prefixes() == DEFAULT_READ_PREFIXES
    result = registry_service.read(r"HKLM\SYSTEM\CurrentControlSet\Services")
    assert result["code"] == REGISTRY_READ_FORBIDDEN, result
    assert spy.calls == []


def test_corrupt_read_allowlist_env_is_discarded_recovery(monkeypatch, spy):
    monkeypatch.setenv(ENV_VAR_READ, r"???bad,,,HKCU\Software\..\Evil\,")
    assert allowed_read_prefixes() == DEFAULT_READ_PREFIXES
    result = registry_service.read(ALLOWED_TEMP, "Theme")
    assert result["ok"] is True, result
    assert spy.calls == [(ALLOWED_TEMP, "Theme")]


def test_hive_alias_outside_allowlist_still_denied(spy):
    result = registry_service.read(r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft")
    assert result["code"] == REGISTRY_READ_NOT_ALLOWED, result
    assert spy.calls == []


@pytest.mark.parametrize(
    "name",
    [
        "DefaultPassword", "AltDefaultPassword", "ProxyPassword", "passwd",
        "ClientSecret", "StoredCredential", "PrivateKeyBlob", "ApiKey",
        "AccessToken", "DigitalProductId",
    ],
)
def test_a_credential_value_is_refused_wherever_it_lives(spy, name):
    result = registry_service.read(ALLOWED_TEMP, name)
    assert result["ok"] is False, result
    assert result["denied"] is True, result
    assert result["code"] == REGISTRY_VALUE_FORBIDDEN, result
    assert spy.calls == []


def test_the_refusal_says_which_term_matched(spy):
    result = registry_service.read(ALLOWED_TEMP, "ProxyPassword")
    assert "PASSWORD" in result["error"], result


@pytest.mark.parametrize("term", SECRET_VALUE_TERMS)
def test_every_declared_term_actually_blocks(spy, term):
    result = registry_service.read(ALLOWED_TEMP, f"My{term.title()}Value")
    assert result["ok"] is False, (term, result)
    assert result["code"] == REGISTRY_VALUE_FORBIDDEN, (term, result)


def test_the_short_name_of_a_family_is_covered_too(spy):
    for value_name in ("ProductId", "DigitalProductId", "DigitalProductId4"):
        result = registry_service.read(ALLOWED_TEMP, value_name)
        assert result["ok"] is False, (value_name, result)
        assert result["code"] == REGISTRY_VALUE_FORBIDDEN, (value_name, result)


@pytest.mark.parametrize(
    "value_name",
    ["API_KEY", "Proxy-Password", "default.password", "client secret", "PRIVATE_KEY", "priv_key", "Digital Product Id"],
)
def test_a_separator_does_not_get_a_secret_past_the_filter(spy, value_name):
    result = registry_service.read(ALLOWED_TEMP, value_name)
    assert result["ok"] is False, (value_name, result)
    assert result["code"] == REGISTRY_VALUE_FORBIDDEN, (value_name, result)


def test_the_separator_trick_does_not_work_through_enumeration_either(monkeypatch):
    backend = SpyBackend({"Theme": "dark", "API_KEY": "sk-live-1", "Proxy-Password": "hunter2"})
    monkeypatch.setattr(registry_service, "get_backend", lambda: backend)
    result = registry_service.read(ALLOWED_TEMP)
    assert result["withheld"] == ["API_KEY", "Proxy-Password"], result
    assert "sk-live-1" not in repr(result)
    assert "hunter2" not in repr(result)


def test_enumeration_does_not_hand_over_what_a_named_read_refuses(monkeypatch):
    backend = SpyBackend({
        "Theme": "dark", "LastUser": "alice",
        "ProxyPassword": "hunter2", "ApiKey": "sk-live-abcdef",
    })
    monkeypatch.setattr(registry_service, "get_backend", lambda: backend)
    result = registry_service.read(ALLOWED_TEMP)
    assert result["ok"] is True, result
    assert "ProxyPassword" not in result["values"], result
    assert "ApiKey" not in result["values"], result
    assert result["values"]["Theme"] == "dark", result
    assert result["values"]["LastUser"] == "alice", result


def test_what_was_withheld_is_declared_not_hidden(monkeypatch):
    backend = SpyBackend({"Theme": "dark", "ProxyPassword": "hunter2"})
    monkeypatch.setattr(registry_service, "get_backend", lambda: backend)
    result = registry_service.read(ALLOWED_TEMP)
    assert result["withheld"] == ["ProxyPassword"], result


def test_a_key_with_nothing_secret_carries_no_withheld_field(spy):
    result = registry_service.read(ALLOWED_TEMP)
    assert result["ok"] is True, result
    assert "withheld" not in result, result


def test_ordinary_reads_under_allowlist_work(spy):
    result = registry_service.read(r"HKCU\Software\ContosoCRM", "Theme")
    assert result["ok"] is True, result
    assert result["value"] == "dark", result


@pytest.mark.parametrize(
    "path",
    [r"HKCU\Software\ContosoCRM", r"HKCU\Software\WinOsLayer\Temp", ALLOWED_TEMP],
)
def test_paths_inside_read_allowlist_reach_the_backend(spy, path):
    registry_service.read(path)
    assert spy.calls


def test_a_prefix_match_does_not_swallow_a_longer_name_on_denylist(spy):
    result = registry_service.read(r"HKLM\SAMPLE\Things")
    assert result["code"] == REGISTRY_READ_NOT_ALLOWED, result
    assert spy.calls == []


def test_the_gate_holds_for_a_caller_that_never_touches_the_api(spy):
    result = registry_service.read(r"HKLM\SECURITY\Policy")
    assert result["denied"] is True, result
    assert spy.calls == []


def test_the_gate_holds_for_non_allowlisted_direct_caller(spy):
    result = registry_service.read(r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion")
    assert result["code"] == REGISTRY_READ_NOT_ALLOWED, result
    assert spy.calls == []


def test_the_api_answers_403_not_200_for_denylist(client, auth_headers):
    response = client.get(
        "/v1/registry", headers=auth_headers,
        params={"path": r"HKLM\SECURITY\Policy\Secrets"},
    )
    assert response.status_code == 403, response.text


def test_the_api_answers_403_for_outside_allowlist(client, auth_headers):
    response = client.get(
        "/v1/registry", headers=auth_headers,
        params={"path": r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion"},
    )
    assert response.status_code == 403, response.text


def test_the_api_still_serves_an_allowed_read(client, auth_headers):
    response = client.get(
        "/v1/registry", headers=auth_headers,
        params={"path": r"HKCU\Software\ContosoCRM", "name": "Theme"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["value"] == "dark", response.text


def test_the_api_refuses_a_credential_value_name(client, auth_headers):
    response = client.get(
        "/v1/registry", headers=auth_headers,
        params={"path": r"HKCU\Software\ContosoCRM", "name": "DefaultPassword"},
    )
    assert response.status_code == 403, response.text
