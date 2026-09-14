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

## Mapping requisiti N001

Copertura dichiarata dalla scheda #67: R01 R42 T04 T05 T08 T10 T11 T12
W001 L001 G01 G02 G03 G19 G25 — tracciati nella matrice #67; questo documento
ne fissa il contratto di **stato/evidenza/scope**, non ne certifica gli effetti
OS (quelli restano in #21 H63-N001 W/L quando eseguiti).
