"""La Virtual API espone solo capability il cui effetto e' stato osservato.

L'endpoint generico ``/actions/{name}`` resta la superficie di gestione usata
anche per provare un'azione. La Virtual API e' invece il contratto OpenAPI
per-app: pubblicare li' un'azione appena scoperta trasformerebbe di nuovo
``esiste`` in ``funziona``.
"""
from __future__ import annotations

from windows_os_api.apps.adapters.engine import (
    create_adapter,
    generate_adapter_openapi,
    get_adapter,
    load_persisted_adapters,
    reset_adapters,
    verify_and_record,
)
from windows_os_api.apps.adapters.verification import VERIFIED
from windows_os_api.apps.schema.generator import app_openapi
from windows_os_api.backends.factory import get_backend


APP = "contoso-crm"


def _edit_action():
    return next(a for a in get_adapter(APP).actions if a.control_type == "Edit")


def test_discovered_actions_are_not_published_as_working_capabilities(tmp_sandbox):
    adapter = create_adapter(APP, hwnd=1001)

    assert adapter.actions, "premessa: l'ispezione deve aver scoperto delle azioni"
    assert all(a.verification is None for a in adapter.actions)
    assert generate_adapter_openapi(adapter)["paths"] == {}


def test_only_an_exact_verified_verdict_enters_the_virtual_api(tmp_sandbox):
    adapter = create_adapter(APP, hwnd=1001)
    states = [VERIFIED, "FAILED", "BLOCKED", "UNSUPPORTED", "UNSTABLE"]
    for action, state in zip(adapter.actions, states, strict=False):
        action.verification = {"state": state, "checked_at": 123.0}
    if len(adapter.actions) > len(states):
        adapter.actions[len(states)].verification = "VERIFIED"  # malformato

    schema = generate_adapter_openapi(adapter)
    expected = {
        f"/v1/apps/{APP}/actions/{adapter.actions[0].name}"
    }

    assert set(schema["paths"]) == expected
    operation = schema["paths"].popitem()[1]["post"]
    assert operation["x-verification-state"] == VERIFIED


def test_http_virtual_api_appears_after_real_verification_and_matches_the_route(
    client, auth_headers
):
    created = client.post(
        f"/v1/apps/{APP}/adapter", headers=auth_headers, json={"hwnd": 1001}
    )
    assert created.status_code == 200, created.text
    assert client.get(
        f"/v1/apps/{APP}/openapi.json", headers=auth_headers
    ).json()["paths"] == {}

    action = _edit_action()
    verified = client.post(
        f"/v1/apps/{APP}/actions/{action.name}/verify",
        headers=auth_headers,
        json={"times": 2},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["verification"]["state"] == VERIFIED

    schema = client.get(
        f"/v1/apps/{APP}/openapi.json", headers=auth_headers
    ).json()
    path = f"/v1/apps/{APP}/actions/{action.name}"
    assert set(schema["paths"]) == {path}
    body_schema = schema["paths"][path]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert body_schema["required"] == ["params"]
    assert body_schema["properties"]["params"]["required"] == ["value"]

    invoked = client.post(
        path,
        headers=auth_headers,
        json={"params": {"value": "chiamata-dalla-virtual-api"}},
    )
    assert invoked.status_code == 200, invoked.text
    assert invoked.json()["ok"] is True, invoked.text


def test_failure_removes_the_path_and_recovery_restores_it(
    tmp_sandbox, monkeypatch
):
    adapter = create_adapter(APP, hwnd=1001)
    action = _edit_action()
    path = f"/v1/apps/{APP}/actions/{action.name}"

    first = verify_and_record(APP, action.name)
    assert first["verification"]["state"] == VERIFIED, first
    assert set(adapter.openapi["paths"]) == {path}

    backend = get_backend()
    with monkeypatch.context() as failure:
        failure.setattr(
            backend,
            "set_ui_value",
            lambda automation_id, value: {"ok": True},
        )
        failed = verify_and_record(APP, action.name)

    assert failed["verification"]["state"] == "FAILED", failed
    assert adapter.openapi["paths"] == {}

    recovered = verify_and_record(APP, action.name)
    assert recovered["verification"]["state"] == VERIFIED, recovered
    assert set(adapter.openapi["paths"]) == {path}


def test_verified_virtual_api_survives_restart_without_reusing_the_window(
    tmp_sandbox,
):
    create_adapter(APP, hwnd=1001)
    action = _edit_action()
    result = verify_and_record(APP, action.name)
    assert result["verification"]["state"] == VERIFIED, result

    reset_adapters()
    report = load_persisted_adapters()
    restored = get_adapter(APP)

    assert report["restored"] == [APP]
    assert restored is not None
    assert restored.bound is False
    assert set(app_openapi(APP)["paths"]) == {
        f"/v1/apps/{APP}/actions/{action.name}"
    }
