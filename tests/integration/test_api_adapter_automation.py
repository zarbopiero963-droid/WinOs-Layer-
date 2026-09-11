"""Apps, adapters, workflows, agent via HTTP."""
from windows_os_api.apps.ui_inspector.service import find_by_automation_id
from windows_os_api.backends.factory import get_backend


def test_discover_and_adapter_flow(client, auth_headers):
    apps = client.post("/v1/apps/discover", headers=auth_headers).json()["apps"]
    assert any(a["id"] == "contoso-crm" for a in apps)
    created = client.post("/v1/apps/contoso-crm/adapter", headers=auth_headers, json={"hwnd": 1001})
    assert created.status_code == 200
    assert len(created.json()["actions"]) >= 3
    actions = client.get("/v1/apps/contoso-crm/actions", headers=auth_headers).json()["actions"]
    set_email = next(a for a in actions if "email" in a["name"])
    inv = client.post(
        f"/v1/apps/contoso-crm/actions/{set_email['name']}",
        headers=auth_headers,
        json={"params": {"value": "demo@contoso.it"}},
    )
    assert inv.json()["ok"] is True
    oapi = client.get("/v1/apps/contoso-crm/openapi.json", headers=auth_headers).json()
    assert oapi["info"]["title"].startswith("Contoso")
    assert len(oapi["paths"]) >= 1


def test_verify_action_http_observes_rolls_back_persists_and_audits(
    client, auth_headers, admin_headers
):
    created = client.post(
        "/v1/apps/contoso-crm/adapter", headers=auth_headers, json={"hwnd": 1001}
    )
    assert created.status_code == 200, created.text
    actions = client.get(
        "/v1/apps/contoso-crm/actions", headers=auth_headers
    ).json()["actions"]
    edit = next(action for action in actions if action["control_type"] == "Edit")
    backend = get_backend()
    original = find_by_automation_id(
        backend.get_ui_tree(1001), edit["automation_id"]
    )["value"]

    response = client.post(
        f"/v1/apps/contoso-crm/actions/{edit['name']}/verify",
        headers=auth_headers,
        json={"times": 2},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] is True, result
    assert result["recorded"] is True, result
    assert result["persisted"] is True, result
    assert result["verification"]["state"] == "VERIFIED", result
    assert result["verification"]["attempts"] == 2, result
    restored = find_by_automation_id(
        backend.get_ui_tree(1001), edit["automation_id"]
    )["value"]
    assert restored == original, "HTTP verification left test data in the app"

    actions_after = client.get(
        "/v1/apps/contoso-crm/actions", headers=auth_headers
    ).json()["actions"]
    recorded = next(action for action in actions_after if action["name"] == edit["name"])
    assert recorded["verification"]["state"] == "VERIFIED", recorded

    audit_entries = client.get("/v1/audit", headers=admin_headers).json()
    entries = audit_entries.get("entries", audit_entries)
    assert any(entry.get("action") == "adapter.verify" for entry in entries), entries


def test_verify_action_http_rejects_unbounded_repetitions(client, auth_headers):
    client.post("/v1/apps/contoso-crm/adapter", headers=auth_headers, json={})
    actions = client.get(
        "/v1/apps/contoso-crm/actions", headers=auth_headers
    ).json()["actions"]
    edit = next(action for action in actions if action["control_type"] == "Edit")

    for times in (0, 11):
        response = client.post(
            f"/v1/apps/contoso-crm/actions/{edit['name']}/verify",
            headers=auth_headers,
            json={"times": times},
        )
        assert response.status_code == 422, response.text

def test_workflow_generate_and_intent(client, auth_headers):
    client.post("/v1/apps/contoso-crm/adapter", headers=auth_headers, json={})
    wf = client.post("/v1/workflows/generate", headers=auth_headers, json={"text": "new customer", "app_id": "contoso-crm"}).json()
    assert wf["confidence"] >= 0.8
    assert len(wf["steps"]) >= 1
    intent = client.post("/v1/intent", headers=auth_headers,
                         json={"text": "nuovo cliente", "app_id": "contoso-crm"}).json()
    assert intent["parsed"]["intent"] == "create_customer"
    agent = client.post("/v1/agent/run", headers=auth_headers,
                        json={"goal": "search", "app_id": "contoso-crm"}).json()
    assert agent["status"] in ("completed", "planned")

def test_reason_heal_plan_metrics(client, auth_headers):
    client.post("/v1/apps/contoso-crm/adapter", headers=auth_headers, json={})
    reason = client.post("/v1/ui/reason", headers=auth_headers, json={"query": "save"}).json()
    assert reason["matched_element"]["automation_id"] == "btn.save"
    heal = client.post("/v1/ui/heal", headers=auth_headers, json={"automation_id": "btn.save"}).json()
    assert heal["ok"] is True
    plan = client.post("/v1/plan", headers=auth_headers,
                       json={"text": "export", "app_id": "contoso-crm"}).json()
    assert "workflow" in plan
    metrics = client.get("/v1/metrics", headers=auth_headers).json()
    assert "counters" in metrics
    assert metrics["counters"].get("http.requests", 0) >= 1

def test_trust_and_sandbox_http(client, admin_headers, auth_headers):
    manifest = {"app_id": "contoso-crm", "version": "1"}
    signed = client.post("/v1/trust/sign", headers=admin_headers, json={"manifest": manifest}).json()
    assert "signature" in signed
    ver = client.post("/v1/trust/verify", headers=auth_headers, json={"manifest": manifest, "signature": signed["signature"]}).json()
    assert ver["valid"] is True
    client.put("/v1/sandbox/policy", headers=auth_headers, json={
        "app_id": "contoso-crm", "denied_actions": ["click_btn_save"], "max_risk": "low"
    })
    client.post("/v1/apps/contoso-crm/adapter", headers=auth_headers, json={})
    # find save action and ensure deny
    actions = client.get("/v1/apps/contoso-crm/actions", headers=auth_headers).json()["actions"]
    save = next(a for a in actions if "save" in a["name"] and a["control_type"] == "Button")
    # Update policy with actual action name
    client.put("/v1/sandbox/policy", headers=auth_headers, json={
        "app_id": "contoso-crm", "denied_actions": [save["name"]], "max_risk": "high"
    })
    denied = client.post(f"/v1/apps/contoso-crm/actions/{save['name']}", headers=auth_headers, json={})
    assert denied.status_code == 403


# ---------------------------------------------------------------------------
# `app_id` is required on every body that carries it (issue #6)
# ---------------------------------------------------------------------------
# It used to default to "contoso-crm". A request that never named an application
# was accepted and applied to the demo CRM — so "do this" without "to what" got
# a 200 and a side effect, instead of a question back.
def test_bodies_without_an_app_id_are_refused(client, auth_headers):
    client.post("/v1/apps/contoso-crm/adapter", headers=auth_headers, json={})
    for path, body in [
        ("/v1/intent", {"text": "nuovo cliente"}),
        ("/v1/agent/run", {"goal": "search"}),
        ("/v1/workflows/generate", {"text": "new customer"}),
        ("/v1/plan", {"text": "create new customer"}),
        ("/v1/workflows/record/start", {"name": "demo"}),
    ]:
        r = client.post(path, headers=auth_headers, json=body)
        assert r.status_code == 422, f"{path} accepted a body with no app_id: {r.text}"
        # FastAPI names the field it is missing; a 422 that did not would leave
        # the caller to guess which of several fields was wrong.
        assert "app_id" in r.text, f"{path} did not name the missing field: {r.text}"


def test_the_same_bodies_work_when_the_app_is_named(client, auth_headers):
    """The refusal is of the omission, not of the request."""
    client.post("/v1/apps/contoso-crm/adapter", headers=auth_headers, json={})
    for path, body in [
        ("/v1/intent", {"text": "nuovo cliente", "app_id": "contoso-crm"}),
        ("/v1/agent/run", {"goal": "search", "app_id": "contoso-crm"}),
        ("/v1/workflows/generate", {"text": "new customer", "app_id": "contoso-crm"}),
        ("/v1/plan", {"text": "create new customer", "app_id": "contoso-crm"}),
        ("/v1/workflows/record/start", {"name": "demo", "app_id": "contoso-crm"}),
    ]:
        r = client.post(path, headers=auth_headers, json=body)
        assert r.status_code == 200, f"{path} refused a complete body: {r.text}"


def test_an_empty_app_id_is_not_a_way_around_the_requirement(client, auth_headers):
    """Required-but-blank is the same mistake with a different spelling.

    The 422 above only covers the missing key. `""` passes Pydantic's type
    check, so the refusal here has to come from the validator at
    `create_adapter` — which is why that check does not live in the route.
    """
    client.post("/v1/apps/contoso-crm/adapter", headers=auth_headers, json={})
    r = client.post("/v1/agent/run", headers=auth_headers,
                    json={"goal": "search", "app_id": "   "})
    assert r.status_code >= 400, f"a blank app_id was accepted: {r.text}"
