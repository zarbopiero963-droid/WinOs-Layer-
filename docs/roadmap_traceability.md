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
baked-in keys (placeholders absent → 401 unless explicitly configured);
``require_auth=false`` yields anonymous **VIEWER** (never anonymous ADMIN).
WS uses the same resolve as REST. MCP tool-level gating remains N020
(helper is shared).

```bash
pytest tests/unit/test_auth_n011.py tests/unit/test_permissions.py -q
pytest tests/security/test_security_hard.py -q
```

Full installed W/L H63-N011 not claimed PASS here (MANUAL / #21).


## N012 — Rotazione, revoca e isolamento user/app/session

Contratto (#67 / H63-N012, B-AUTH): runtime revocation registry (key still in Settings → 401); session issue + revoke; key rotation without role escalation (ADMIN / SoD); app_scopes + workflow `owner_subject` isolation; `before_step` / `assert_active` at side-effect boundary so mid-play revoke denies the next step. WS drops on revoked key/session. Rate-limit / remote HTTP → N013; MCP tool revoke → N020.

```bash
pytest tests/unit/test_auth_n012.py tests/unit/test_auth_n011.py -q
pytest tests/security/test_security_hard.py -q
```

Full installed W/L H63-N012 not claimed PASS here (MANUAL / #21).

## N013 — Rate limit, quote, body cap e confine remoto HTTP

Contratto (#67 / H63-N013, B-AUTH): wire process-wide rate limit + concurrency gate + Content-Length body cap; localhost default; remote opt-in with **explicit** CORS origins (never implicit `*`); peer-only remote guard — `X-Forwarded-For` / `Forwarded` never grant loopback. TLS termination remains operator concern (opt-in remote).

```bash
pytest tests/unit/test_remote_rate_n013.py tests/unit/test_security_core.py -q
```

Full installed W/L H63-N013 not claimed PASS here (MANUAL / #21).

## N014 — API Registry: modello e stati autorevoli

Contratto (#67 / H63-N014, B-API, Q08): in-memory API registry schema (#61) with six authoritative statuses VERIFIED · PARTIAL · RESTRICTED · UNSUPPORTED · ERROR · DISABLED; **GENERATED ≠ VERIFIED**; missing/false/stale verification metadata never yields VERIFIED; deterministic duplicate ids; `schema_version`; D3/lifecycle → status mapping helpers. Coverage: R31 R33 W061 W070 L061 L070 S61-01–S61-03 G28 G29. Persistence → N015; GET /v1/apis → N016; execution → N017.

```bash
pytest tests/unit/test_api_registry_n014.py -q
```

Full installed W/L H63-N014 not claimed PASS here (MANUAL / #21).

## N015 — API Registry: persistenza atomica e proiezioni

Contratto (#67 / H63-N015, B-API, Q08/Q15): versioned crash-safe store (`api_registry.json` + `.bak`, `WINOS_API_REGISTRY_STORE`); atomic tmp→rename; fail-closed on truncated/corrupt/wrong version; bak recovery; id/natural-key mismatch rejected; reload re-runs VERIFIED gate (never trust persisted status or adapter/workflow **manifest** alone). Projections for native / adapter / workflow|plugin → honest non-VERIFIED unless registry evidence. Coverage: R33 W070 L070 S61-13 S61-22 G29. Catalog → N016; gateway → N017.

```bash
pytest tests/unit/test_api_registry_n015.py tests/unit/test_api_registry_n014.py -q
```

Full installed W/L H63-N015 not claimed PASS here (MANUAL / #21).


## N016 — Catalogo API: lista, dettaglio, ricerca e filtri

Contratto (#67 / H63-N016, B-API, Q08): `GET /v1/apis` + detail with `limit`/`offset` pagination (no duplicate ids across pages), search `q`, filters `source` / `application_id`|`app` / `status` / `permission`|`permissions`, authorized visibility via app_scopes (ADMIN sees all; cross-app filter → 403). Missing record **404** distinct from registry unavailable **503**. Catalog helpers under `apps/api_registry/catalog.py`; REST `api/rest/apis.py`. Coverage: R31 R32 W062 W070 L062 L070 S61-04–S61-05 G28 G29. Gateway → N017.

```bash
pytest tests/unit/test_api_registry_n016.py tests/unit/test_api_registry_n015.py tests/unit/test_api_registry_n014.py -q
```

Full installed W/L H63-N016 not claimed PASS here (MANUAL / #21).

## N017 — Execution gateway unico e policy non autoassegnabile

Contratto (#67 / H63-N017, B-API, Q02/Q08): shared `authorize_execution` / `execute_via_gateway` for REST (and MCP/GUI later); **no implicit `create_adapter` on invoke**; DISABLED / revoked / ERROR / RESTRICTED / UNSUPPORTED / stale VERIFIED refuse execute as **security** blocks; absent adapter is **operational** (404); engine sandbox deny remains security via gated path; does not auto-assign permissions/policy. Coverage: R32 R35 R36 R47 W065 L065 S61-06 S61-12 G08 G13 G20 G29. Verified result → N018.

```bash
pytest tests/unit/test_api_registry_n017.py tests/unit/test_api_registry_n016.py tests/unit/test_api_registry_n015.py tests/unit/test_api_registry_n014.py -q
```

Full installed W/L H63-N017 not claimed PASS here (MANUAL / #21).


## N018 — Risultato verificato e API Test

Contratto (#67 / H63-N018, B-VERIFY, Q07/Q08): execution linked to **independent postcondition** + **verification_id**; ``POST /v1/apis/{api_id}/test`` returns ``success`` only when effect is observed; insufficient proof → ``PARTIAL`` / ``verified=false``; HTTP 200 / invoke ``ok`` alone is **not** success when state unchanged; failed rollback blocks VERIFIED and must not mint ``verification_id``; forged VERIFIED without id demoted on adapter load. Coverage: R25 R32 W066-W067 L066-L067 S61-07 S61-20-S61-21 G12 G29. OpenAPI deterministico → N019.

```bash
pytest tests/unit/test_api_registry_n018.py tests/security/test_capability_verification.py -q
```

Full installed W/L H63-N018 not claimed PASS here (MANUAL / #21).


## N019 — OpenAPI deterministico e mapping CRUD reale

Contratto (#67 / H63-N019, B-API, Q09): rich OpenAPI (input/output/errors/scopes/risk/auth/version); **CRUD only if verified**; stable canonical export (prefer zero volatile fields) with **unique operationIds** (include ``app_id`` / ``api_id``); unverified capabilities absent; invalid schema rejected; ``app_openapi`` fail-closed (no implicit ``create_adapter``); registry ``GET /v1/apis/openapi.json`` exports VERIFIED records only; VERIFIED registry paths are merged into root ``/openapi.json``. Coverage: R31 R32 W061-W064 W067 L061-L064 L067 S61-08 S61-23 G28 G29. MCP dinamico → N020.

```bash
pytest tests/unit/test_api_registry_n019.py tests/unit/test_api_registry_n018.py -q
```

Full installed W/L H63-N019 not claimed PASS here (MANUAL / #21).

## N020 — MCP dinamico: protocollo, tool e revoca

Contratto (#67 / H63-N020, B-MCP, Q09): ``tools/list`` from **VERIFIED** registry (+ platform baseline); stable tool names/schemas; **runtime revoke** (DISABLED omitted + ``tools/call`` denied); protocol negotiation (supported versions only); ``invoke_action`` uses gateway **without** implicit ``create_adapter``. Coverage: R36 W071-W073 W075 L071-L073 L075 S61-09 S61-24 G20 G29. MCP resources → N021.

```bash
pytest tests/unit/test_mcp_n020.py tests/unit/test_mcp.py -q
```

Full installed W/L H63-N020 not claimed PASS here (MANUAL / #21).


## N021 — MCP risorse, eventi e parità REST

Contratto (#67 / H63-N021, B-MCP, Q09/Q10): ``resources/list`` + ``resources/read`` from **VERIFIED** registry (``winos://api/{id}``); secret redaction; ``resources.listChanged`` + pending ``notifications/resources/list_changed`` (tools twin); ``params._meta.app_scopes`` cross-user parity with REST catalog; ``handle_message`` → ``-32700`` on malformed JSON; same capability via gateway (REST) and ``tools/call`` (MCP) shares effect/deny identity. Coverage: R36 W074-W075 L074-L075 S61-09 S61-24 G29. Control Center → N022.

```bash
pytest tests/unit/test_mcp_n021.py tests/unit/test_mcp_n020.py tests/unit/test_mcp.py -q
```

Full installed W/L H63-N021 not claimed PASS here (MANUAL / #21).


## N022 — Control Center tab OS/App/API e catalogo

Contratto (#67 / H63-N022, B-UI, Q13): shell a tab **Dashboard | OS Layer | Apps | API**; discover reale con stato adapter ``nessuno`` / ``unbound`` / ``bound`` (``bound`` se ``list_adapters.bound`` o ``hwnd`` presente; ``unbound`` se adapter senza hwnd / disk-restored); catalogo e dettaglio con **API solo VERIFIED** (``GET /v1/apis?status=VERIFIED``); filtri ``q``/method/source/application_id; metadata ostile via ``textContent`` (anti-XSS); API Key UI **non** precompilata. Try it → N023; Create→Verify→Publish → N024. Coverage: R40 W092 L092 S61-10–S61-11 S61-25 G28 G29.

```bash
pytest tests/unit/test_control_center_n022.py -q
```

Full installed W/L H63-N022 not claimed PASS here (MANUAL / #21).

## N023 — Control Center Try it, test e diagnosi API

Contratto (#67 / H63-N023, B-UI, Q08/Q13): **Try it / API Test** su dettaglio API → ``POST /v1/apis/{id}/test`` (gateway N018) con ``X-API-Key`` da ``#key``; successo solo se ``success`` / ``verification.verified`` (mai HTTP 200 da solo). **View workflow** (``GET /v1/workflows``), **View adapter** (``GET /v1/apps/adapters/list`` + app/actions; ``nessuno``/``unbound``/``bound``), **View log** (``GET /v1/audit`` con redaction secret), **Disable** via ``POST /v1/apis/{id}/disable`` (``ADAPTER_MANAGE`` → ``set_status(DISABLED)`` + audit; Try it fail-closed). Risultati via ``textContent``; API Key **non** precompilata. Create→Verify→Publish → N024. Coverage: R40 W092 L092 S61-11 S61-25 G28 G29.

```bash
pytest tests/unit/test_control_center_n023.py tests/unit/test_control_center_n022.py -q
```

Full installed W/L H63-N023 not claimed PASS here (MANUAL / #21).

## N024 — Control Center Create → Verify → Publish

Contratto (#67 / H63-N024, B-UI, Q08/Q13): form **Create → Verify → Publish** senza consenso implicito da GUI. ``POST /v1/apis`` crea sempre candidato **PARTIAL** (``ADAPTER_MANAGE``, audit ``apis.create``). Verify → ``POST /v1/apis/{id}/test`` con ``update_registry_on_pass:false``: PASS scrive ``verification_id``+``last_verified_at`` restando **PARTIAL** (mai VERIFIED silenzioso). ``POST /v1/apis/{id}/publish`` promuove a VERIFIED solo con evidence fresca (``authorize_verified_status``); altrimenti **409**. DISABLED non pubblica senza recovery (re-verify → PARTIAL → publish). Disable (N023) ritira endpoint. UI: confirm esplicito su Publish; risultati ``textContent``; ``#key`` non precompilata. Coverage: R22 R31 R40 W092 L092 S61-10–S61-11 G28 G29.

```bash
pytest tests/unit/test_control_center_n024.py tests/unit/test_control_center_n023.py tests/unit/test_control_center_n022.py -q
```

Full installed W/L H63-N024 not claimed PASS here (MANUAL / #21).

## N025 — SDK/export dei contratti API

Contratto (#67 / H63-N025, B-API, Q09): export schema + **client SDK Python** dal contratto **pubblicato/VERIFIED** (OpenAPI N019) senza duplicare il motore di esecuzione né inventare endpoint; ``GET /v1/apis/sdk.py`` e ``GET /v1/apps/{app_id}/sdk.py``; output deterministico (nessun timestamp/secret); client propaga **403**, supporta **timeout**, e dopo revoke/demote la rigenerazione omette l’op (live fail-closed). Coverage: R31 W063 L063 G01 G28. Segreti AI GUI → N026.

```bash
pytest tests/unit/test_api_registry_n025.py tests/unit/test_api_registry_n019.py -q
```

Full installed W/L H63-N025 not claimed PASS here (MANUAL / #21).

## N026 — Segreti AI e configurazione sicura della GUI

Contratto (#67 / H63-N026, B-UI, Q02/Q13): proteggere credenziali AI persistite con storage **owner-only** (Unix ``chmod 600`` + dir ``0700``; Windows ACL best-effort via pywin32/``icacls``, senza hard-fail su Linux CI), salvataggio atomico (``os.open(..., 0o600)`` + ``os.replace``), rotazione/clear senza eco raw key, fail-closed su JSON corrotto e su write failure (runtime non half-applied; HTTP detail senza secret); GUI senza key precompilate (`dev`/`admin`/AI) e senza render secret da GET. Coverage: R03 R40 R48 G09 G19 G20. Confine egress AI → N027.

```bash
pytest tests/unit/test_ai_settings_n026.py tests/security/test_ai_key_masking.py tests/unit/test_ai_provider.py -q
```

Full installed W/L H63-N026 not claimed PASS here (MANUAL / #21).

## N027 — Confine egress AI e input non fidato

Contratto (#67 / H63-N027, B-AI, Q02/Q13/Q15): validare base URL / redirect / DNS / provider (https-only, no credentials in URL, block private/link-local/metadata IP literals and DNS→private, default hosts ``api.openai.com`` / ``api.anthropic.com`` / ``openrouter.ai`` + custom solo se SSRF-safe); ``follow_redirects=False`` e deny 3xx; timeout/cancel fail-closed con ``spent=False``; prompt/UI/log come dati — redact API-key-like patterns dai body, mai ``api_key`` nel testo prompt; prompt-injection non abilita tool (gate ignora ``force_execute``/``bypass_gate``/``enable_tools``; confidence ≠ permesso). Coverage: R28 R35 R38 G08 G09 G19 G20.

```bash
pytest tests/unit/test_ai_egress_n027.py tests/unit/test_ai_provider.py tests/unit/test_agent_execution_gate.py -q
```

Full installed W/L H63-N027 not claimed PASS here (MANUAL / #21).

## N028 — Manifest checksum affidabile

Contratto (#67 / H63-N028, B-UPD, Q14): checksums manifest **non** elenca se stesso anche con input espliciti; path/label **univoci** (basename duplicati senza ``root=`` → fail; con ``root=`` relative path disambiguati); file mancanti/alterati → fail al generate e al ``verify_checksum_manifest`` indipendente; write atomico; regen×2 byte-identico. Helper ``scripts/stage_release_checksums.sh`` (+ ``docs/patches/n028_release_yml.patch``) genera ``SHA256SUMS.txt`` senza self-hash (fail-closed); wiring in ``.github/workflows/release.yml`` richiede OAuth scope ``workflow`` per il push dal box (apply owner se token bot non lo ha). ``UpdateManager`` verifica package directory via manifest embedded (identità = hash del manifest, non self-hash interno); ``apply(verify=False)`` resta ammesso (enforcement trust → N029/N030). Coverage: R44 R45 G05. Unit evidence H63-N028; installed W/L MANUAL_ONLY su #21.

```bash
pytest tests/unit/test_checksum_manifest_n028.py tests/unit/test_update_manager.py tests/unit/test_build_installer.py tests/unit/test_installed_product_preflight_n003.py tests/unit/test_installer_smoke.py -q
```

Full installed W/L H63-N028 not claimed PASS here (MANUAL / #21).

## N029 — Trust adapter production e identità publisher

Contratto (#67 / H63-N029, B-UPD, Q14): HMAC-dev al massimo ``dev``; production = Ed25519 + keystore (``key_id``, publisher binding, revoke); ``verified_publisher`` self-asserted ignorato; forged/revoked/bad → ``unsigned``; load deny se claim production non riverificata; execute deny su ``TRUST_INSUFFICIENT``. Coverage: R48 W097 L097 G09 G20. Enforcement tamper-after-load → N030.

```bash
pytest tests/unit/test_trust_n029.py tests/unit/test_trust_and_sandbox.py -q
```

Full installed W/L H63-N029 not claimed PASS here (MANUAL / #21).

## N030 — Enforcement trust su load ed esecuzione

Contratto (#67 / H63-N030, B-VERIFY, Q08/Q14): riverifica firma sul contenuto adapter corrente (actions incluse) prima di invoke; tamper → ``TRUST_INSUFFICIENT`` + demote VERIFIED→INVALID; reload firma valida ripristina uso; claim production su disco non riverificabile → skip load. Coverage: R22 R33 R48 W032 W097 L032 L097 G19 G20. Release discovery → N031.

```bash
pytest tests/unit/test_trust_n030.py tests/unit/test_trust_n029.py -q
```

Full installed W/L H63-N030 not claimed PASS here (MANUAL / #21).

## N031 — Release discovery e download verificato

Contratto (#67 / H63-N031, B-UPD, Q14): channel HTTPS JSON (version/artifact_url/sha256); download size-capped in staging; hash verify (N028); no private/loopback hosts; refuse downgrade/same version unless ``allow_downgrade``. Coverage: R45 G09 G20. Update transazionale Windows → N032.

```bash
pytest tests/unit/test_update_discovery_n031.py tests/unit/test_update_manager.py tests/unit/test_checksum_manifest_n028.py -q
```

Full installed W/L H63-N031 not claimed PASS here (MANUAL / #21).


## N032 — Update transazionale Windows

Contratto (#67 / H63-N032, B-UPD, Q01/Q14): percorso distribuito **sempre** verify (``apply(verify=False)`` rifiutato); sequenza stop → replace → start → health con hook iniettabili; fallimento a ogni confine → rollback; rollback elimina file introdotti dall'update (zero orfani); health ambiguo ≠ successo. Coverage: R39 R41 R45 W091 W093-W094 G11 G20. Shared core ``_apply_os_transactional`` (N033). Update transazionale Linux → N033.

```bash
pytest tests/unit/test_update_transactional_n032.py tests/unit/test_update_transactional_n033.py tests/unit/test_update_discovery_n031.py tests/unit/test_update_manager.py tests/unit/test_checksum_manifest_n028.py -q
```

Full installed W H63-N032 not claimed PASS here (MANUAL_ONLY / #21).


## N033 — Update transazionale Linux

Contratto (#67 / H63-N033, B-UPD, Q01/Q14): stessa state machine di N032 per systemd/user + albero install Linux — preflight verify → permissions → backup → stop → replace → start → health; always verify; fallimento a ogni confine → rollback senza orfani; health ambiguo ≠ successo; **permissions denied fail closed** prima del replace; journal/stages coerenti; hook ``stop_service``/``start_service``/``health_check`` iniettabili (no live systemctl in CI). Coverage: R41 R45 L091 L093-L094 G09 G20. API pubblica: ``apply_linux_transactional``.

```bash
pytest tests/unit/test_update_transactional_n033.py tests/unit/test_update_transactional_n032.py tests/unit/test_update_discovery_n031.py tests/unit/test_update_manager.py -q
```

Full installed L H63-N033 not claimed PASS here (MANUAL_ONLY / #21).


## N034 — Installer Windows: identità, key e lifecycle

Contratto (#67 / H63-N034, B-PKG, Q01/Q03): preservare fix #54; **SetupMutex** wizard singola istanza; key **CSPRNG** + **ACL** (SYSTEM/Administrators/LocalService); servizio **NT AUTHORITY\\LocalService** + harden ACL su logs/tmp/sandbox; **AppDirectory**/binPath assoluto; reject weak/dev keys; setup key autentica, key dev no. Coverage: R39 R41 W091 W093 G10 G11 G21. Linux FakeBackend/key load → N035.

```bash
pytest tests/unit/test_installer_identity_n034.py tests/unit/test_windows_service_lifecycle.py tests/unit/test_installer_smoke.py tests/unit/test_build_installer.py -q
python scripts/build_installer.py validate
```

Full installed W H63-N034 not claimed PASS here (MANUAL_ONLY / #21).

## N035 — Installer Linux: backend reale + key load

Contratto (#67 / H63-N035, B-PKG, Q01/Q03): path install Linux distribuito senza FakeBackend come default; systemd + CLI caricano la key via ``--api-key-file`` (mai stampare il secret); path user vs system separati; reject weak/dev keys (reuse ``WEAK_API_KEYS`` / ``assert_release_api_key``); default ``WINOS_BACKEND=auto`` → LinuxBackend. Coverage: R39 R41 L091 G10 G11 G21. Out of scope: N036, N037; non chiudere #67/#63/#21; non toccare N005 D6 / workflows.

```bash
pytest tests/unit/test_installer_linux_n035.py tests/unit/test_installer_identity_n034.py tests/unit/test_build_installer.py tests/unit/test_installer_smoke.py -q
python scripts/build_installer.py validate
```

Full installed L H63-N035 not claimed PASS here (MANUAL_ONLY / #21).


## N036 — Pacchetti Linux e compatibilità upgrade

Contratto (#67 / H63-N036, B-PKG, Q01/Q14): produrre **deb / rpm / AppImage** con metadati e lifecycle coerenti (install/upgrade/uninstall); preservare ``api_key.txt`` su upgrade; ``install.sh --upgrade`` + ``uninstall.sh --keep-data``; templates sotto ``installer/linux/packaging/``; Flatpak resta proposta #64 (non implementato). Coverage: R01 R41 R44 L093 G03. Out of scope: N037 signing, Flatpak, secrets, bypass gates, chiusura #67/#63/#21, N005 D6.

```bash
pytest tests/unit/test_linux_packages_n036.py tests/unit/test_installer_linux_n035.py tests/unit/test_build_installer.py -q
python scripts/build_installer.py validate
python scripts/build_installer.py package-linux --format deb rpm appimage
```

Full installed L H63-N036 (ogni formato su distro reale) not claimed PASS here (MANUAL_ONLY / #21).


## N037 — Firma EXE/Setup e artifact release

Contratto (#67 / H63-N037, B-PKG, Q14/#10): attestation/manifest (version, publisher, sha256, signing_status ``unsigned|authenticode|attested``); optional Ed25519 publisher binding via ``WINOS_RELEASE_SIGNING_KEY``; optional Authenticode via owner PFX secrets only (**no cert purchase**); independent ``verify-release`` fail-closed on tamper / wrong publisher / bad signature; Inno SignTool documented but optional so ISCC works without cert; release.yml wiring via ``docs/patches/n037_release_yml.patch`` (workflow scope). Coverage: R44 R48 W093 W100 L100 G05 G09. Out of scope: Flatpak, N005 D6, N038+, chiusura #67/#63/#21, claim installed Authenticode PASS senza cert.

```bash
pytest tests/unit/test_release_signing_n037.py -q
python scripts/build_installer.py validate
python scripts/build_installer.py attest-release
python scripts/build_installer.py verify-release
```

Full installed W H63-N037 Authenticode/SmartScreen not claimed PASS here (MANUAL_ONLY / #21 / #10).


## N038 — Event schema e redaction prima del fan-out

Contratto (#67 / H63-N038, B-BUS, Q11): allowlist type/schema/provenienza; payload limitati (byte/depth/keys); redaction secret-keys ricorsiva **prima** di history/subscribe; tipi sconosciuti (es. ``admin.granted``) e payload oversize **respinti** senza fan-out; ``agent.goal.proposed`` / ``executed`` / ``denied`` distinti (goal non implica autorizzazione). Coverage: R37 R49 W005 W068 L005 L068 G14 G15 G16. Out of scope: N039 thread-safe/backpressure, N040 WS app isolation, N041 webhooks, N005 D6, chiusura #67/#63/#21.

```bash
pytest tests/unit/test_event_schema_n038.py tests/unit/test_event_bus.py tests/unit/test_fixture_isolation_n002.py tests/unit/test_agent_execution_gate.py -q
```

Full installed W/L H63-N038 not claimed PASS here (MANUAL_ONLY / #21).

## N039 — Bus thread-safe, backpressure e lifecycle

Contratto (#67 / H63-N039, B-BUS, Q11/Q12): handoff thread→loop non bloccante
(``call_soon_threadsafe``); limiti queue/subscriber/byte con **drop osservabile**
(``EventBus.dropped`` / ``stats()``); ``stop()`` sentinel + ``reset_event_bus``
senza deadlock/leak; recovery con bus fresco. Coverage: R37 R49 W068 L068 G15 G16 G17.
Out of scope: N040 WS app isolation, N041 webhooks, N005 D6, chiusura #67/#63/#21.

```bash
pytest tests/unit/test_event_bus_n039.py tests/unit/test_event_bus.py tests/unit/test_event_schema_n038.py -q
```

Full installed W/L H63-N039 not claimed PASS here (MANUAL_ONLY / #21).


## N040 — WebSocket auth e isolamento applicazioni

Contratto (#67 / H63-N040, B-BUS, Q02/Q10): eliminare secret in URL; auth
browser-safe (``X-API-Key`` header, ``Sec-WebSocket-Protocol: winos.apikey.<b64url>``,
oppure first-message ``{"type":"auth","api_key":...}``); ``?api_key=`` **sempre
negato** (4401); Origin/peer guard allineato a N013; RBAC ``SYSTEM_READ``;
filtri server-side app/subject (fail-closed su ``app_scopes`` vuoti per eventi
app-bound; admin vede tutto); idle/flood cap per connessione; cleanup subscriber
su disconnect; revoca mid-stream N012 invariata. Coverage: R37 R38 W068 L068 G14 G16.
Out of scope: N041 webhooks, N005 D6, chiusura #67/#63/#21, claim H63-N040
installed W/L PASS.

```bash
pytest tests/unit/test_websocket_n040.py tests/unit/test_auth_n011.py tests/unit/test_auth_n012.py tests/unit/test_event_bus_n039.py -q
```

Meccanismo documentato in ``windows_os_api/api/websocket/bus.py`` (module docstring).
Full installed W/L H63-N040 not claimed PASS here (MANUAL_ONLY / #21).
