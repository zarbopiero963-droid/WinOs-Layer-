r"""E2E hard: un EXE mai descritto attraversa l'intera pipeline adapter.

Il programma WinForms viene compilato in ``tmp_path`` durante il test. Nome
dell'EXE, titolo, AccessibleName e Name del controllo contengono un UUID creato
in quel momento: nessuna fixture, tabella di sinonimi o selector nel repository
puo' conoscerli in anticipo.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.windows, pytest.mark.e2e, pytest.mark.requires_display]


def _compile_unknown_winforms_exe(tmp_path: Path, token: str) -> Path:
    exe = tmp_path / f"WinosUnknown{token}.exe"
    source = tmp_path / "unknown.cs"
    script = tmp_path / "compile.ps1"
    label = f"runtime-field-{token}"
    control = f"runtime_control_{token}"
    title = f"Runtime Unknown {token}"
    source.write_text(
        f"""
using System;
using System.Drawing;
using System.Windows.Forms;

public sealed class UnknownForm : Form
{{
    public UnknownForm()
    {{
        Text = "{title}";
        Width = 640;
        Height = 240;
        var box = new TextBox();
        box.Name = "{control}";
        box.AccessibleName = "{label}";
        box.Text = "original-{token}";
        box.Left = 30;
        box.Top = 60;
        box.Width = 520;
        Controls.Add(box);
    }}

    [STAThread]
    public static void Main()
    {{
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new UnknownForm());
    }}
}}
""".strip(),
        encoding="utf-8",
    )
    script.write_text(
        "param([string]$Source, [string]$Output)\n"
        "Add-Type -Path $Source -ReferencedAssemblies "
        "'System.Windows.Forms.dll','System.Drawing.dll' -OutputAssembly $Output "
        "-OutputType WindowsApplication\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-Source",
            str(source),
            "-Output",
            str(exe),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert exe.is_file(), completed.stdout
    return exe


def _walk(node: dict):
    yield node
    for child in node.get("children") or []:
        yield from _walk(child)


def _wait_for_window(backend, pid: int) -> int:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        owned = next((w for w in backend.list_windows() if w.get("pid") == pid), None)
        if owned is not None:
            return int(owned["hwnd"])
        time.sleep(0.1)
    pytest.fail(f"the runtime-generated EXE exposed no window for pid {pid}")


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows (win32)")
def test_runtime_generated_unknown_exe_reaches_a_verified_virtual_api(
    tmp_path, monkeypatch, auth_headers
):
    from fastapi.testclient import TestClient

    from windows_os_api.apps.adapters.engine import (
        get_adapter,
        reset_adapters,
        verify_and_record,
    )
    from windows_os_api.apps.automation.actions import discover_actions
    from windows_os_api.apps.discovery import service as discovery
    from windows_os_api.apps.reasoning.offline import reason
    from windows_os_api.apps.schema.generator import app_openapi
    from windows_os_api.apps.workflows.generator import generate_workflow
    from windows_os_api.backends.factory import get_backend, reset_backend
    from windows_os_api.core.runtime.app import create_app
    from windows_os_api.core.runtime.config import get_settings

    token = uuid.uuid4().hex[:12]
    label = f"runtime-field-{token}"
    exe = _compile_unknown_winforms_exe(tmp_path, token)
    app_id = exe.stem.casefold()

    monkeypatch.setenv("WINOS_BACKEND", "windows")
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(tmp_path / "adapters"))
    get_settings.cache_clear()
    reset_backend()
    reset_adapters()
    backend = get_backend()
    proc = subprocess.Popen([str(exe)])
    try:
        hwnd = _wait_for_window(backend, proc.pid)

        discovered = []
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            discovered = discovery.discover()
            if any(a["id"] == app_id for a in discovered):
                break
            time.sleep(0.1)
        app = next((a for a in discovered if a["id"] == app_id), None)
        assert app is not None, f"running unknown EXE was not discovered: {exe}"
        assert Path(app["path"]).resolve() == exe.resolve()
        assert app["source"] == "running-process"

        tree = backend.get_ui_tree(hwnd)
        node = next(
            (
                item
                for item in _walk(tree)
                if item.get("name") == label and item.get("value") is not None
            ),
            None,
        )
        assert node is not None, json.dumps(tree, indent=2, ensure_ascii=False)

        semantic = reason(f"imposta {label}", hwnd=hwnd)
        assert semantic["matched_element"] is not None, semantic
        assert semantic["matched_element"]["automation_id"] == node["automation_id"]
        assert any(
            item["element"]["automation_id"] == node["automation_id"]
            for item in semantic["suggestions"]
        ), semantic

        actions = discover_actions(app_id, hwnd=hwnd)
        action = next(
            a for a in actions if a["automation_id"] == node["automation_id"]
        )
        assert action["control_type"] == "Edit", action
        assert "contoso" not in json.dumps(actions).casefold()

        workflow = generate_workflow(f"imposta {label}", app_id=app_id, hwnd=hwnd)
        assert [step.action for step in workflow.steps] == [action["name"]]
        assert set(workflow.steps[0].params) == set(action["params"])

        verified = verify_and_record(app_id, action["name"], times=2)
        assert verified["verification"]["state"] == "VERIFIED", verified
        adapter = get_adapter(app_id)
        assert adapter is not None and adapter.persisted is True

        path = f"/v1/apps/{app_id}/actions/{action['name']}"
        assert set(app_openapi(app_id)["paths"]) == {path}

        with TestClient(create_app(get_settings())) as client:
            remote_schema = client.get(
                f"/v1/apps/{app_id}/openapi.json", headers=auth_headers
            )
            assert remote_schema.status_code == 200, remote_schema.text
            assert set(remote_schema.json()["paths"]) == {path}
            invoked = client.post(
                path,
                headers=auth_headers,
                json={"params": {"value": f"final-{token}"}},
            )
            assert invoked.status_code == 200, invoked.text
            assert invoked.json()["ok"] is True, invoked.text

        fresh = backend.get_ui_tree(hwnd)
        changed = next(
            item for item in _walk(fresh) if item.get("automation_id") == node["automation_id"]
        )
        assert changed["value"] == f"final-{token}", changed
    finally:
        reset_adapters()
        reset_backend()
        get_settings.cache_clear()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
