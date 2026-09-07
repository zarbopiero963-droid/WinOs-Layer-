"""Apps, adapters, workflows, agent via HTTP."""
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

def test_workflow_generate_and_intent(client, auth_headers):
    client.post("/v1/apps/contoso-crm/adapter", headers=auth_headers, json={})
    wf = client.post("/v1/workflows/generate", headers=auth_headers, json={"text": "new customer", "app_id": "contoso-crm"}).json()
    assert wf["confidence"] >= 0.8
    assert len(wf["steps"]) >= 1
    intent = client.post("/v1/intent", headers=auth_headers, json={"text": "nuovo cliente"}).json()
    assert intent["parsed"]["intent"] == "create_customer"
    agent = client.post("/v1/agent/run", headers=auth_headers, json={"goal": "search"}).json()
    assert agent["status"] in ("completed", "planned")

def test_reason_heal_plan_metrics(client, auth_headers):
    client.post("/v1/apps/contoso-crm/adapter", headers=auth_headers, json={})
    reason = client.post("/v1/ui/reason", headers=auth_headers, json={"query": "save"}).json()
    assert reason["matched_element"]["automation_id"] == "btn.save"
    heal = client.post("/v1/ui/heal", headers=auth_headers, json={"automation_id": "btn.save"}).json()
    assert heal["ok"] is True
    plan = client.post("/v1/plan", headers=auth_headers, json={"text": "export"}).json()
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
