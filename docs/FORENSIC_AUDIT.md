# Forensic Audit — WinOs-Layer-
Generated for roadmap PRs #1–#50. Status: `DONE` | `PARTIAL` | `NOT_STARTED`.
Run: `python scripts/forensic_audit.py` (fails if DONE claims lack files/tests).
Tests: `pytest -q` with `WINOS_BACKEND=fake`.

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

## PR6: App discovery (FakeBackend + Windows registry/paths)
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/discovery/service.py`
  - `windows_os_api/backends/fake.py`
  - `windows_os_api/backends/windows.py`
- **Tests:**
  - `tests/unit/test_fake_backend.py`
  - `tests/integration/test_api_adapter_automation.py`
- **How to run:** `pytest tests/unit/test_fake_backend.py -q`

## PR7: Windows manager APIs
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/windows/service.py`
  - `windows_os_api/api/rest/windows.py`
- **Tests:**
  - `tests/integration/test_api_os_layers.py`
- **How to run:** `pytest tests/integration/test_api_os_layers.py -q`

## PR8: UI Automation tree + FakeBackend CRM + Windows UIA stub
- **Status:** DONE
- **Files:**
  - `windows_os_api/apps/ui_inspector/service.py`
  - `windows_os_api/backends/fake.py`
  - `windows_os_api/backends/windows.py`
- **Tests:**
  - `tests/unit/test_fake_backend.py`
  - `tests/unit/test_adapter_engine.py`
- **How to run:** `pytest tests/unit/test_fake_backend.py -q`

## PR9: Mouse/keyboard input
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/input/service.py`
  - `windows_os_api/api/rest/ui.py`
- **Tests:**
  - `tests/integration/test_api_os_layers.py`
- **How to run:** `pytest tests/integration/test_api_os_layers.py -q`

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

## PR14: Network interfaces/connections
- **Status:** DONE
- **Files:**
  - `windows_os_api/os/network/service.py`
  - `windows_os_api/api/rest/network.py`
- **Tests:**
  - `tests/integration/test_api_os_layers.py`
- **How to run:** `pytest tests/integration/test_api_os_layers.py -q`

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
  - `windows_os_api/os/terminal/service.py`
  - `windows_os_api/api/rest/services.py`
- **Tests:**
  - `tests/unit/test_fake_backend.py`
  - `tests/integration/test_api_os_layers.py`
  - `tests/security/test_security_hard.py`
- **How to run:** `pytest tests/unit/test_fake_backend.py -q`

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
  - `windows_os_api/apps/sandbox/permissions.py`
  - `windows_os_api/api/rest/security_routes.py`
- **Tests:**
  - `tests/unit/test_trust_and_sandbox.py`
  - `tests/integration/test_api_adapter_automation.py`
- **How to run:** `pytest tests/unit/test_trust_and_sandbox.py -q`

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
- DONE: 50
- PARTIAL: 0
- NOT_STARTED: 0
