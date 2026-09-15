# N045 — Lock order (verify / invoke / store)

Levels (acquire **low → high** only; never hold a higher level while taking a lower):

| Level | Lock | Held during |
|------:|------|-------------|
| 1 | `_adapters_registry_lock` | Registry insert/replace/clear of `_adapters` |
| 2 | `Adapter._verification_lock` | Probe / record / invoke effect on that adapter |
| 3 | `store._io_lock` | Disk read/write of manifests only |

**Proper vs improper UI under lock**

- Holding **level 2** (per-adapter verification lock) during UI/backend I/O for a
  probe is **proper**: it is the atomic boundary that prevents two probes or an
  external invoke from swapping original/probe values on the same control.
- Holding **level 3** (store I/O) or any process-wide shared lock during UI,
  network, or `await` is **improper** and forbidden.

**Atomic record boundary**

`verify_and_record` keeps level 2 across verify → stamp `action_fp` → mutate
in-memory verdict/OpenAPI → `store.save` (which takes level 3). Callers must
not take level 3 then level 2.
