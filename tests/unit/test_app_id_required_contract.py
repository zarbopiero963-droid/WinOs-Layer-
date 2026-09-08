"""Which application? — `app_id` must be said, not assumed.

Owner decision, issue #6: no `contoso-crm` default anywhere.

`app_id` used to default to `"contoso-crm"` — the fake backend's demo CRM — on
seven production call sites, three of them public surfaces (the REST bodies, the
MCP `agent_run` tool, and `ComputerAgent` itself). A caller who never said which
application they meant did not get an error. They got the demo app, and the
request succeeded against it.

On the fake backend that is a wrong answer. Pointed at a real desktop it is a
request applied to whatever application happens to answer to that name — which
is the same class of mistake as a trading order sent to an unnamed market.

Two halves, and the second is the one that matters:

* **required** — omitting `app_id` is refused, and the refusal says what is
  missing;
* **not empty** — `app_id=""` is the same mistake with a different spelling, and
  a parameter that is required in the signature but accepts `""` is required in
  name only.

The BLOCK direction: restore any of the removed defaults, or drop the emptiness
check, and tests here go red.
"""
from __future__ import annotations

import inspect

import pytest

from windows_os_api.api.mcp import server as mcp_server
from windows_os_api.apps.adapters.engine import create_adapter, get_adapter
from windows_os_api.apps.adapters.validation import AppIdRejected, validate_app_id
from windows_os_api.apps.agent.computer import ComputerAgent
from windows_os_api.apps.automation.actions import discover_actions
from windows_os_api.apps.intent.engine import execute_intent
from windows_os_api.apps.planner.service import plan
from windows_os_api.apps.workflows.generator import generate_workflow


# ---------------------------------------------------------------------------
# The validator itself
# ---------------------------------------------------------------------------
def test_a_real_app_id_passes_through_unchanged():
    assert validate_app_id("contoso-crm") == "contoso-crm"
    assert validate_app_id("notepad.exe") == "notepad.exe"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "   ",
        "\t",
        "\n",
        123,
        0,
        [],
        {},
        True,
    ],
)
def test_anything_that_cannot_name_an_application_is_refused(bad):
    """Empty, blank and non-string values are all "you did not say which app".

    `0` and `True` are in the list on purpose: both are falsy-or-int values that
    a laxer check (`if not app_id`) would treat inconsistently, and neither
    identifies an application.
    """
    with pytest.raises(AppIdRejected):
        validate_app_id(bad)


def test_the_refusal_says_what_is_wrong():
    """An error a caller cannot act on is barely better than no error."""
    with pytest.raises(AppIdRejected) as missing:
        validate_app_id(None)
    assert "required" in str(missing.value)

    with pytest.raises(AppIdRejected) as empty:
        validate_app_id("")
    assert "empty" in str(empty.value)


# ---------------------------------------------------------------------------
# The signatures: no default may come back
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "func,param",
    [
        (ComputerAgent.__init__, "app_id"),
        (execute_intent, "app_id"),
        (plan, "app_id"),
        (generate_workflow, "app_id"),
        (discover_actions, "app_id"),
    ],
)
def test_no_production_entry_point_defaults_the_app_id(func, param):
    """Structural guard, and the direct BLOCK for this PR.

    Restoring `app_id: str = "contoso-crm"` on any of these makes this red. It
    asserts on `inspect.signature` rather than on behaviour because the whole
    defect was that the default produced *working* behaviour — a test that only
    called the function would have passed both before and after.
    """
    sig = inspect.signature(func)
    assert sig.parameters[param].default is inspect.Parameter.empty, (
        f"{func.__qualname__} defaults {param}; a caller who forgets to name an "
        f"application must be told, not given one"
    )


def test_no_production_module_still_hardcodes_the_demo_app():
    """The fake backend may name its own demo app. Nothing else may.

    `backends/fake.py` legitimately contains "contoso-crm" — it *is* the fixture
    that defines that app. A default in any other production module is the
    defect this PR removes.
    """
    import windows_os_api.api.mcp.server
    import windows_os_api.api.rest.workflows
    import windows_os_api.apps.agent.computer
    import windows_os_api.apps.automation.actions
    import windows_os_api.apps.intent.engine
    import windows_os_api.apps.planner.service
    import windows_os_api.apps.workflows.generator

    modules = [
        windows_os_api.api.rest.workflows,
        windows_os_api.api.mcp.server,
        windows_os_api.apps.agent.computer,
        windows_os_api.apps.intent.engine,
        windows_os_api.apps.planner.service,
        windows_os_api.apps.workflows.generator,
        windows_os_api.apps.automation.actions,
    ]
    offenders = []
    for mod in modules:
        source = inspect.getsource(mod)
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # the comments explaining the removal may name it
            if "contoso-crm" in line:
                offenders.append(f"{mod.__name__}:{lineno}: {stripped}")
    assert not offenders, "the demo app is back as a default:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------
def test_the_agent_refuses_to_be_built_without_an_app(tmp_sandbox):
    with pytest.raises(AppIdRejected):
        ComputerAgent("")
    with pytest.raises(AppIdRejected):
        ComputerAgent("   ")


def test_the_agent_still_works_when_the_app_is_named(tmp_sandbox):
    """The change refuses the omission, not the ordinary case."""
    agent = ComputerAgent("contoso-crm")
    assert agent.app_id == "contoso-crm"
    result = agent.run("search customer")
    assert result["goal"] == "search customer"
    assert "gate" in result


def test_no_adapter_is_registered_for_a_rejected_app_id(tmp_sandbox):
    """Fail-closed: a refused build leaves nothing behind.

    If validation ran after `create_adapter`, an adapter keyed on `""` would sit
    in the registry — reachable by anything that also passes `""`, and invisible
    to `list_adapters` readers looking for a name.
    """
    with pytest.raises(AppIdRejected):
        create_adapter("")
    assert get_adapter("") is None
    with pytest.raises(AppIdRejected):
        create_adapter("  ")
    assert get_adapter("  ") is None


# ---------------------------------------------------------------------------
# The MCP surface
# ---------------------------------------------------------------------------
def test_the_mcp_agent_tool_declares_app_id_required():
    """It was the one tool on this server that did not.

    `create_adapter` and `invoke_action` already required it — and `agent_run`
    drives both of those.
    """
    tool = next(t for t in mcp_server.TOOLS if t["name"] == "agent_run")
    assert "app_id" in tool["inputSchema"]["required"]


def test_the_mcp_server_refuses_an_agent_run_with_no_app(tmp_sandbox):
    """And the refusal is a JSON-RPC error naming the field, not a KeyError.

    Before, `arguments.get("app_id", "contoso-crm")` made this call succeed
    against the demo CRM.
    """
    resp = mcp_server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "agent_run", "arguments": {"goal": "search"}},
    })
    assert "error" in resp, resp
    message = resp["error"]["message"]
    assert "app_id" in message
    assert "missing" in message.lower(), message


def test_every_mcp_tool_enforces_the_arguments_it_declares(tmp_sandbox):
    """The enforcement reads the schema, so it cannot drift from it.

    A tool that declares `required` and does not enforce it is documentation,
    not a contract.
    """
    for tool in mcp_server.TOOLS:
        required = tool["inputSchema"].get("required") or []
        if not required:
            continue
        resp = mcp_server.handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool["name"], "arguments": {}},
        })
        assert "error" in resp, f"{tool['name']} accepted an empty argument set"
        for field in required:
            assert field in resp["error"]["message"], (
                f"{tool['name']} did not name the missing {field!r}"
            )


def test_the_mcp_agent_tool_runs_when_the_app_is_named(tmp_sandbox):
    resp = mcp_server.handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "agent_run",
                   "arguments": {"goal": "search", "app_id": "contoso-crm"}},
    })
    assert "result" in resp, resp
