# Roadmap contracts, status families, and traceability (N001)

Formalizes tracking for issue #63 / executive plan #67 / product tests #21.
Does **not** invent new merge gates: it records vocabularies and the CLOSED
chain already stated in those sources and in `AGENTS.md` / `CLAUDE.md`.

## Scope per lotto (Nxxx)

Each N-lotto in #67 is one PR at a time on updated `main`, with:

- **Contratto atteso** from the Phase 0 card (comment linked from #67).
- **files_allowed / files_forbidden** fixed before the patch (candidate B-*
  paths are not a license to rewrite whole modules).
- **Dipendenze** on prior lotti; missing dependency ⇒ dependent capability
  stays disabled/unpublished — not silently marked DONE.
- **Prove** registered in #21 as `H63-Nxxx` (Windows first, then Linux when
  applicable). Documentation-only lots prove contract/traceability; they must
  not invent OS effects.

Out of scope for N001 itself: implementing later N002–N107 behaviours,
secrets, destructive user-data changes, or lowering existing safety gates.

## Tre famiglie di stati

Questa sezione formalizza le **tre famiglie** di stati usate dal piano (lotto/forense, esito test #21, capability), senza inventare gate.

Do not mix these vocabularies. A value from one family must not be used as a
PASS in another.

### 1) Stato lotto / registro forense (`docs/forensic_audit.json`)

| Stato | Significato |
|---|---|
| `NOT_STARTED` | Lotto non avviato (aperto). |
| `PARTIAL` | Lavoro/files presenti ma **catena evidenza incompleta** — resta aperto. |
| `DONE` | Consentito **solo** con `files` + `tests` + `evidence` non vuota. |

`DONE` in this registry is a packaging/trace claim under audit, **not** a
substitute for product CLOSED below. Historical rows that only listed paths
were reclassified to `PARTIAL` by N001.

### 2) Esito test prodotto (#21)

| Stato | Significato |
|---|---|
| `PASS` | Scenario eseguito con expected==actual e oracolo indipendente. |
| `PARTIAL` | Copertura incompleta; non chiude il requisito. |
| `FAIL` | Eseguito e fallito. |
| `BLOCKED_ENV` | Ambiente/prerequisito assente — **mai** contato come PASS. |
| `NOT_RUN` | Non eseguito — resta aperto. |

Protocollo per caso funzionale: SUCCESS/readback + SECURITY BLOCK (stato
invariato) + FAILURE controllato + RECOVERY, con PR/SHA/URL/hash artifact,
log e limiti. Skip, FakeBackend, HTTP 200, `ok=True` non bastano.

### 3) Stato capability / adapter

| Stato | Significato |
|---|---|
| `VERIFIED` | Effetto riletto e contratto soddisfatto. |
| `BLOCKED` | Policy/security ha impedito l’effetto. |
| `UNSUPPORTED` | Fuori capability dichiarata (confine; non è feature DONE). |
| `RESTRICTED` | Consentito solo sotto vincoli espliciti. |
| `UNSTABLE` | Non affidabile — non certificabile come successo. |

## Catena CLOSED (criterio #63)

Una voce operativa si considera **CLOSED** solo con la catena documentata:

```text
IMPLEMENTED
+ REAL SYSTEM TEST
+ SECURITY BLOCK TEST
+ FAILURE/RECOVERY TEST
= CLOSED
```

Non sono CLOSED: «file presente», «PR merged», «test raccolto», HTTP 200,
`ok=True`, o `DONE=50` nel forense basato sulla sola esistenza path.

## Schema `evidence` (forense)

Per `status=DONE` il campo `evidence` è **obbligatorio** e non vuoto.
Deve citare prove riproducibili (comando/exit, run/job CI, SHA, limiti).
Assenza o solo whitespace ⇒ il requisito **resta aperto** (audit FAIL se
ancora marcato DONE).

Stati fuori da `{DONE, PARTIAL, NOT_STARTED}` sono **corrupt/invalid** e
fanno fallire l’audit (non devono diventare PASS).

```bash
python scripts/forensic_audit.py
pytest tests/unit/test_forensic_audit_traceability.py -q
```

## N002 — Fixture isolate e separazione fake/live

Contratto (#67 / H63-N002, famiglie Q00/Q15):

1. Nessun adapter, subscriber, policy o counter ereditato tra test dopo il
   teardown (bus / metrics / rate-limiter / adapter store wipe).
2. Fixture live (`live_backend` / `live_client`) **fail-closed** se il backend
   risolto è `FakeBackend` (`WINOS_ALLOW_FAKE_FALLBACK=false` sul path live).

Helpers: `reset_event_bus`, `reset_metrics`, `reset_limiter`,
`clear_adapter_store`, cablati in `tests/conftest.py` (`tmp_sandbox`) e
`tests/linux/conftest.py` (`linux_backend`).

```bash
pytest tests/unit/test_fixture_isolation_n002.py -q
```

Out of scope: harness prodotto installato (N003), farm matrix completa.

## N003 — Harness del prodotto installato (preflight)

Contratto (#67 / H63-N003, famiglie Q00/Q01) — **scaffolding fail-closed only**:

1. Porta destinazione occupata → blocco (`require_port_free`).
2. `FakeBackend` / `backend==fake` rilevato → blocco.
3. Checksum artifact assente o hash mismatch → blocco.
4. API key di sessione **realmente generata** (`secrets.token_urlsafe`); chiavi
   statiche di suite/smoke rifiutate.

Helpers: `tests/harness/installed_product_preflight.py`
(`run_session_preflight`, raccolta version/hash/backend, stub clients_allowed
tcp/mcp/ws/browser).

```bash
pytest tests/unit/test_installed_product_preflight_n003.py -q
```

**Non** certifica download/install Windows+Linux su prodotto reale (#21), né
MANUAL_ONLY desktop/hardware come PASS. Quelli restano aperti / NEEDS_MANUAL.

Out of scope: altri lotti, segreti reali, bypass gate, farm matrix completa.

## Mapping requisiti N001

Copertura dichiarata dalla scheda #67: R01 R42 T04 T05 T08 T10 T11 T12
W001 L001 G01 G02 G03 G19 G25 — tracciati nella matrice #67; questo documento
ne fissa il contratto di **stato/evidenza/scope**, non ne certifica gli effetti
OS (quelli restano in #21 H63-N001 W/L quando eseguiti).

## N004 — Contratti runtime, capability ed errori

Contratto (#67 / H63-N004, famiglie Q01/Q04) — **health honesty + D3 bare lists**:

1. `GET /v1/health` (and additive `GET /v1/ready`) via `windows_os_api/os/runtime_health.py` must **not** report
   `status: ok` / `ready: true` when the backend is unavailable or config is
   invalid (`BACKEND_UNAVAILABLE` / `CONFIG_INVALID`). Recovery restores `ok`.
   Additive fields: `ready`, `backend`, `error_code`, `reason`. `version` kept.
   HTTP 503 when not ready.
2. Bare lists wrapped via `discover()` (D3): `users.list_users`,
   `display.list_displays`, `processes.list_processes`, `storage.list_drives`.
   REST routes return the envelope directly (same pattern as printers/devices).

```bash
pytest tests/unit/test_n004_health_and_d3_lists.py -q
pytest tests/integration/test_capability_envelope_api.py -q
```

**Out of scope (deferred):** Windows `terminate_process` ownership (N047/N048);
full installed W/L H63-N004; MANUAL_ONLY desktop. Phase 0:
https://github.com/zarbopiero963-droid/WinOs-Layer-/issues/67#issuecomment-5668178895

## N006 — Scrittura registry: default D2 stretto e migrazione

Contratto (#67 / H63-N006, D2 in #64): `DEFAULT_PREFIXES` = only
`HKCU\\Software\\WinOsLayer\\`. Extend via `WINOS_REGISTRY_ALLOWLIST`.
Immutable denylist (`HKLM\\SYSTEM|SECURITY|SAM`) remains non-reopenable by ADMIN/env.

```bash
pytest tests/security/test_registry_write_allowlist.py -q
pytest tests/integration/test_service_control_api.py -q -k registry
```

**Migration:** callers that wrote under generic `HKCU\\Software\\<Other>` must
set `WINOS_REGISTRY_ALLOWLIST`. Full installed W/L H63-N006 not claimed PASS here.

## N007 — SCM Windows: query/start/stop dietro D1

Contratto (#67 / H63-N007): real Windows SCM start/stop/status via pywin32
(`OpenService` / `StartService` / `ControlService` / `QueryServiceStatus`) when
`win32service` is available; `service_control=True` accordingly. Default
allowlist remains **empty** (D1-B default-deny). Ambiguous transitional state
is not success. Restart/timeout/recovery → N008.

```bash
pytest tests/unit/test_windows_scm_control_n007.py -q
pytest tests/security/test_windows_scm_n007.py tests/security/test_service_control_unsupported.py tests/security/test_service_allowlist.py -q
```

Full installed W/L H63-N007 not claimed PASS here. Do not stop CI runner
system services in tests.

## N008 — SCM restart, timeout e recovery

Contratto (#67 / H63-N008): Windows SCM ``restart`` with limited waits for
terminal state, incompatible-transition ``conflict``, distinct
``timeout`` / ``not_found`` / ``permission_denied`` / operational codes,
handle cleanup (no orphans), and Linux systemctl error-code parity. REST maps
404/409/504. Default allowlist remains **empty** (D1-B). Ambiguous/timeout ≠ success.

```bash
pytest tests/unit/test_windows_scm_n008.py tests/unit/test_windows_scm_control_n007.py tests/unit/test_linux_scm_n008_codes.py -q
pytest tests/security/test_windows_scm_security_n008.py tests/security/test_windows_scm_n007.py -q
pytest tests/integration/test_service_control_n008_http.py -q
```

Full installed W/L H63-N008 not claimed PASS here. Do not stop CI runner
system services in tests.

## N009 — Audio Windows: enumerazione e lettura reale

Contratto (#67 / H63-N009, D4): WASAPI read of devices / default / volume / mute
via ``comtypes`` (no ``pycaw``). Session missing → ``CAPABILITY_UNAVAILABLE``.
Empty active-endpoint list is honest zero. Default missing → ``device_absent``.
Mutations + restore → N010.

```bash
pytest tests/unit/test_windows_audio_n009.py tests/unit/test_capability_flags_contract.py -q
pytest tests/security/test_service_control_unsupported.py tests/security/test_power_action_denial.py -q
```

Full installed W/L H63-N009 not claimed PASS here (needs dedicated audio device).

## N010 — Audio Windows: volume/mute con ripristino

Contratto (#67 / H63-N010, D4): gated ``set_volume`` / ``set_mute`` via
``comtypes``/WASAPI (no ``pycaw``) with readback; restore prior volume+mute
even on set/verify failure; invalid percent (not int 0–100) rejected without
mutating; session missing → ``session_unavailable`` (not forever unsupported);
deny/failure leaves prior state restored when possible. Ambiguous ≠ success.
N009 owns read-only honesty.

```bash
pytest tests/unit/test_windows_audio_n010.py tests/unit/test_windows_audio_n009.py -q
pytest tests/unit/test_capability_flags_contract.py -q
```

Full installed W/L H63-N010 not claimed PASS here (needs dedicated audio device / MANUAL).

## N011 — Identità e ruoli assegnabili a tutti gli ingressi

Contratto (#67 / H63-N011, B-AUTH): stable principal via shared
``build_auth_context`` / ``resolve_role``; four assignable roles
(VIEWER/OPERATOR/AUTOMATOR/ADMIN) through distinct key lists; secure
``secrets.compare_digest`` key compare; release defaults carry **no**
baked-in keys; documentation placeholders refused unless
``WINOS_ALLOW_PLACEHOLDER_API_KEYS=true``; ``require_auth=false`` yields
anonymous **VIEWER** (never anonymous ADMIN). WS uses the same resolve
as REST. MCP tool-level gating remains N020 (helper is shared).

```bash
pytest tests/unit/test_auth_n011.py tests/unit/test_permissions.py -q
pytest tests/security/test_security_hard.py -q
```

Full installed W/L H63-N011 not claimed PASS here (MANUAL / #21).

