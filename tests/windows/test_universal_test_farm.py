r"""Gate 7: certify the unknown-app pipeline against five real Windows stacks.

Every case builds or stages an application at runtime with a random executable
name, window title, control identity and accessible label. The common contract
then proves discovery -> UIA -> reasoning -> workflow -> repeated verified
write/rollback -> persisted adapter -> authenticated HTTP invocation -> readback.

Electron and Qt are intentionally optional in the ordinary Windows suite, but
``WINOS_REQUIRE_TEST_FARM=1`` makes every missing dependency a hard failure in
the dedicated Universal Test Farm workflow.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.windows,
    pytest.mark.e2e,
    pytest.mark.requires_display,
    pytest.mark.test_farm,
]

Builder = Callable[[Path, str], Path]


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 180) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    assert completed.returncode == 0, (
        f"command failed ({completed.returncode}): {command!r}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )


def _dependency_or_skip(message: str) -> None:
    if os.getenv("WINOS_REQUIRE_TEST_FARM") == "1":
        pytest.fail(message)
    pytest.skip(message)


def _build_win32(root: Path, token: str) -> Path:
    exe = root / f"WinosWin32{token}.exe"
    source = _write(
        root / "native.cpp",
        f"""
#include <windows.h>

LRESULT CALLBACK WindowProc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {{
    if (message == WM_DESTROY) {{
        PostQuitMessage(0);
        return 0;
    }}
    return DefWindowProc(hwnd, message, wparam, lparam);
}}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {{
    const wchar_t CLASS_NAME[] = L"WinosNativeClass{token}";
    WNDCLASS wc = {{}};
    wc.lpfnWndProc = WindowProc;
    wc.hInstance = instance;
    wc.lpszClassName = CLASS_NAME;
    RegisterClass(&wc);

    HWND window = CreateWindowEx(
        0, CLASS_NAME, L"Runtime Win32 {token}", WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 680, 230, nullptr, nullptr, instance, nullptr
    );
    CreateWindowEx(
        0, L"STATIC", L"runtime-field-{token}", WS_CHILD | WS_VISIBLE,
        28, 28, 580, 24, window, nullptr, instance, nullptr
    );
    CreateWindowEx(
        WS_EX_CLIENTEDGE, L"EDIT", L"original-{token}",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL,
        28, 62, 580, 28, window, reinterpret_cast<HMENU>(1001), instance, nullptr
    );
    ShowWindow(window, show);
    UpdateWindow(window);
    MSG msg = {{}};
    while (GetMessage(&msg, nullptr, 0, 0) > 0) {{
        TranslateMessage(&msg);
        DispatchMessage(&msg);
    }}
    return 0;
}}
""",
    )
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / (
        "Microsoft Visual Studio/Installer/vswhere.exe"
    )
    if not vswhere.is_file():
        _dependency_or_skip(f"Visual C++ locator is missing: {vswhere}")
    completed = subprocess.run(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    installation = completed.stdout.strip()
    if completed.returncode != 0 or not installation:
        _dependency_or_skip("Visual C++ Build Tools were not found")
    vcvars = Path(installation) / "VC/Auxiliary/Build/vcvars64.bat"
    command = _write(
        root / "compile-native.cmd",
        f'call "{vcvars}" >nul && cl.exe /nologo /EHsc /DUNICODE /D_UNICODE '
        f'"{source}" /Fe:"{exe}" user32.lib gdi32.lib\n'
    )
    # A batch file avoids cmd.exe /S quote rewriting when vcvars lives below
    # "Program Files". The first farm run proved the direct /C string path is
    # otherwise parsed as a quoted command name on the hosted Windows image.
    _run(["cmd.exe", "/d", "/c", str(command)], cwd=root)
    assert exe.is_file()
    return exe


def _build_winforms(root: Path, token: str) -> Path:
    exe = root / f"WinosWinForms{token}.exe"
    source = _write(
        root / "winforms.cs",
        f"""
using System;
using System.Drawing;
using System.Windows.Forms;

public sealed class FarmForm : Form
{{
    public FarmForm()
    {{
        Text = "Runtime WinForms {token}";
        Width = 680;
        Height = 230;
        var box = new TextBox {{
            Name = "runtime_control_{token}",
            AccessibleName = "runtime-field-{token}",
            Text = "original-{token}",
            Left = 28,
            Top = 62,
            Width = 580
        }};
        Controls.Add(box);
    }}

    [STAThread]
    public static void Main()
    {{
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new FarmForm());
    }}
}}
""",
    )
    script = _write(
        root / "compile.ps1",
        """
param([string]$Source, [string]$Output)
Add-Type -Path $Source -ReferencedAssemblies `
  'System.Windows.Forms.dll','System.Drawing.dll' `
  -OutputAssembly $Output -OutputType WindowsApplication
""",
    )
    _run(
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
        ]
    )
    assert exe.is_file()
    return exe


def _build_wpf(root: Path, token: str) -> Path:
    assembly = f"WinosWpf{token}"
    project = _write(
        root / "wpf.csproj",
        f"""
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>WinExe</OutputType>
    <TargetFramework>net8.0-windows</TargetFramework>
    <UseWPF>true</UseWPF>
    <AssemblyName>{assembly}</AssemblyName>
    <Nullable>enable</Nullable>
  </PropertyGroup>
</Project>
""",
    )
    _write(
        root / "Program.cs",
        f"""
using System;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;

public sealed class FarmWindow : Window
{{
    public FarmWindow()
    {{
        Title = "Runtime WPF {token}";
        Width = 680;
        Height = 230;
        var box = new TextBox {{
            Name = "runtime_control_{token}",
            Text = "original-{token}",
            Width = 580,
            Height = 30,
            Margin = new Thickness(28, 62, 28, 80)
        }};
        AutomationProperties.SetName(box, "runtime-field-{token}");
        Content = box;
    }}
}}

public static class Program
{{
    [STAThread]
    public static void Main()
    {{
        var app = new Application();
        app.Run(new FarmWindow());
    }}
}}
""",
    )
    _run(
        [
            "dotnet",
            "publish",
            str(project),
            "-c",
            "Release",
            "-r",
            "win-x64",
            "--self-contained",
            "false",
            "--nologo",
        ],
        cwd=root,
    )
    exe = root / "bin/Release/net8.0-windows/win-x64/publish" / f"{assembly}.exe"
    assert exe.is_file()
    return exe


def _build_electron(root: Path, token: str) -> Path:
    configured = os.getenv("WINOS_ELECTRON_EXE", "")
    runtime = Path(configured) if configured else Path("__missing_electron__")
    if not runtime.is_file():
        _dependency_or_skip(
            "WINOS_ELECTRON_EXE must identify the pinned Electron runtime"
        )
    exe = runtime.with_name(f"WinosElectron{token}.exe")
    shutil.copy2(runtime, exe)
    _write(
        root / "package.json",
        json.dumps({"name": f"winos-electron-{token}", "main": "main.js"}),
    )
    _write(
        root / "main.js",
        f"""
const {{ app, BrowserWindow }} = require('electron');
app.setName('WinosElectron{token}');
app.whenReady().then(() => {{
  const window = new BrowserWindow({{
    width: 680,
    height: 230,
    title: 'Runtime Electron {token}',
    webPreferences: {{ contextIsolation: true, sandbox: true }}
  }});
  window.loadFile('index.html');
}});
app.on('window-all-closed', () => app.quit());
""",
    )
    _write(
        root / "index.html",
        f"""
<!doctype html>
<html lang="en">
  <body>
    <div>Electron test field</div>
    <input id="runtime_control_{token}" aria-label="runtime-field-{token}"
           value="original-{token}" style="width:580px;height:30px">
  </body>
</html>
""",
    )
    return exe


def _build_qt(root: Path, token: str) -> Path:
    try:
        import PyInstaller  # noqa: F401
        import PySide6  # noqa: F401
    except ImportError:
        _dependency_or_skip("PySide6 and PyInstaller are required for the Qt farm app")
    name = f"WinosQt{token}"
    source = _write(
        root / "qt_app.py",
        f"""
import sys
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

app = QApplication(sys.argv)
window = QWidget()
window.setWindowTitle("Runtime Qt {token}")
window.resize(680, 230)
layout = QVBoxLayout(window)
field = QLineEdit("original-{token}")
field.setObjectName("runtime_control_{token}")
field.setAccessibleName("runtime-field-{token}")
layout.addWidget(field)
window.show()
sys.exit(app.exec())
""",
    )
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--windowed",
            "--name",
            name,
            "--distpath",
            str(root / "dist"),
            "--workpath",
            str(root / "build"),
            "--specpath",
            str(root),
            str(source),
        ],
        cwd=root,
        timeout=300,
    )
    exe = root / "dist" / name / f"{name}.exe"
    assert exe.is_file()
    return exe


FRAMEWORKS: tuple[tuple[str, Builder], ...] = (
    ("win32", _build_win32),
    ("winforms", _build_winforms),
    ("wpf", _build_wpf),
    ("electron", _build_electron),
    ("qt", _build_qt),
)


def _walk(node: dict) -> Iterator[dict]:
    yield node
    for child in node.get("children") or []:
        yield from _walk(child)


def _wait_for_window(backend, pid: int, framework: str) -> int:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        owned = next((window for window in backend.list_windows() if window["pid"] == pid), None)
        if owned is not None:
            return int(owned["hwnd"])
        time.sleep(0.1)
    pytest.fail(f"{framework} exposed no top-level window for pid {pid}")


def _wait_for_edit(backend, hwnd: int, expected: str) -> tuple[dict, dict]:
    last_tree: dict = {}
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        last_tree = backend.get_ui_tree(hwnd)
        node = next(
            (
                item
                for item in _walk(last_tree)
                if str(item.get("control_type", "")).casefold() in {"edit", "document"}
                and item.get("value") == expected
                and item.get("automation_id")
            ),
            None,
        )
        if node is not None:
            return last_tree, node
        time.sleep(0.1)
    pytest.fail(
        f"no writable UIA control exposed value {expected!r}: "
        + json.dumps(last_tree, indent=2, ensure_ascii=False)
    )


def _launch_command(framework: str, exe: Path, root: Path) -> list[str]:
    if framework == "electron":
        return [str(exe), str(root), "--disable-gpu", "--no-sandbox"]
    return [str(exe)]


def _kill_process_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            timeout=20,
            check=False,
        )
    elif proc.poll() is None:
        proc.kill()


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows (win32)")
@pytest.mark.parametrize(
    ("framework", "builder"),
    FRAMEWORKS,
    ids=[name for name, _builder in FRAMEWORKS],
)
def test_real_framework_reaches_verified_virtual_api(
    framework: str,
    builder: Builder,
    tmp_path: Path,
    monkeypatch,
    auth_headers,
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
    case_root = tmp_path / framework
    case_root.mkdir()
    exe = builder(case_root, token)
    app_id = exe.stem.casefold()
    original = f"original-{token}"

    monkeypatch.setenv("WINOS_BACKEND", "windows")
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(case_root / "adapters"))
    get_settings.cache_clear()
    reset_backend()
    reset_adapters()
    backend = get_backend()
    proc = subprocess.Popen(_launch_command(framework, exe, case_root), cwd=case_root)
    try:
        hwnd = _wait_for_window(backend, proc.pid, framework)

        discovered: list[dict] = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            discovered = discovery.discover()
            if any(app["id"] == app_id for app in discovered):
                break
            time.sleep(0.1)
        app = next((item for item in discovered if item["id"] == app_id), None)
        assert app is not None, f"{framework} runtime EXE was not discovered: {exe}"
        assert Path(app["path"]).resolve() == exe.resolve()
        assert app["source"] == "running-process"

        _, node = _wait_for_edit(backend, hwnd, original)
        semantic_key = node.get("name") or node["automation_id"]
        semantic = reason(f"imposta {semantic_key}", hwnd=hwnd)
        assert semantic["matched_element"] is not None, semantic
        assert semantic["matched_element"]["automation_id"] == node["automation_id"]

        actions = discover_actions(app_id, hwnd=hwnd)
        action = next(
            item for item in actions if item["automation_id"] == node["automation_id"]
        )
        assert action["control_type"] == "Edit", action
        assert "contoso" not in json.dumps(actions).casefold()

        workflow = generate_workflow(
            f"imposta {semantic_key}", app_id=app_id, hwnd=hwnd
        )
        assert [step.action for step in workflow.steps] == [action["name"]]
        assert set(workflow.steps[0].params) == set(action["params"])

        verified = verify_and_record(app_id, action["name"], times=2)
        assert verified["verification"]["state"] == "VERIFIED", verified
        adapter = get_adapter(app_id)
        assert adapter is not None and adapter.persisted is True

        path = f"/v1/apps/{app_id}/actions/{action['name']}"
        assert set(app_openapi(app_id)["paths"]) == {path}
        with TestClient(create_app(get_settings())) as client:
            response = client.post(
                path,
                headers=auth_headers,
                json={"params": {"value": f"final-{token}"}},
            )
            assert response.status_code == 200, response.text
            assert response.json()["ok"] is True, response.text

        _, changed = _wait_for_edit(backend, hwnd, f"final-{token}")
        assert changed["automation_id"] == node["automation_id"]
        print(f"TEST FARM CERTIFIED: {framework} ({exe.name})")
    finally:
        reset_adapters()
        reset_backend()
        get_settings.cache_clear()
        _kill_process_tree(proc)
