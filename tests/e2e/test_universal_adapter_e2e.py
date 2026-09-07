"""E2E: full universal adapter path on FakeBackend CRM fixture."""
import pytest

@pytest.mark.e2e
def test_e2e_crm_adapter_path(client, auth_headers):
    # 1 discover
    apps = client.post("/v1/apps/discover", headers=auth_headers).json()["apps"]
    assert any(a["name"] == "Contoso CRM" for a in apps)

    # 2 create adapter from UI tree
    ad = client.post("/v1/apps/contoso-crm/adapter", headers=auth_headers, json={"hwnd": 1001}).json()
    assert ad["app_id"] == "contoso-crm"
    assert len(ad["actions"]) >= 5

    # 3 inspect UI
    tree = client.get("/v1/ui/tree", headers=auth_headers, params={"hwnd": 1001}).json()
    assert tree["automation_id"] == "ContosoCRM.Main"

    # 4 semantic reason
    reason = client.post("/v1/ui/reason", headers=auth_headers, json={"query": "customer name"}).json()
    assert reason["matched_element"]["automation_id"] == "field.customer_name"

    # 5 fill fields via virtual API
    actions = {a["name"]: a for a in client.get("/v1/apps/contoso-crm/actions", headers=auth_headers).json()["actions"]}
    name_action = next(n for n in actions if "customer_name" in n)
    email_action = next(n for n in actions if "email" in n)
    assert client.post(f"/v1/apps/contoso-crm/actions/{name_action}", headers=auth_headers,
                       json={"params": {"value": "Alice Rossi"}}).json()["ok"]
    assert client.post(f"/v1/apps/contoso-crm/actions/{email_action}", headers=auth_headers,
                       json={"params": {"value": "alice@contoso.it"}}).json()["ok"]

    # 6 auto workflow
    wf = client.post("/v1/workflows/generate", headers=auth_headers,
                     json={"text": "create new customer", "app_id": "contoso-crm"}).json()
    assert wf["confidence"] >= 0.85
    assert len(wf["rollback"]) >= 0

    # 7 agent
    agent = client.post("/v1/agent/run", headers=auth_headers, json={"goal": "nuovo cliente", "app_id": "contoso-crm"}).json()
    assert agent["intent"]["intent"] == "create_customer"

    # 8 per-app openapi
    oapi = client.get("/v1/apps/contoso-crm/openapi.json", headers=auth_headers).json()
    assert any("/actions/" in p for p in oapi["paths"])

    # 9 control center served
    home = client.get("/")
    assert home.status_code == 200
    assert "Control Center" in home.text
