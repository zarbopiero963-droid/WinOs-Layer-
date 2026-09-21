"""N046 / H63-N046 — budget risorse e recovery runtime (Q12/Q15).

Coverage refs: R02 R05 R39 R49 W002 L002 G09 G17 G19.
Installed W/L: MANUAL_ONLY (never claim PASS here).

Un ciclo runtime possiede le proprie risorse. Quando esce, il ciclo successivo
non deve trovare niente di vivo: né il bus del ciclo precedente, né i suoi
subscriber, né il lock di istanza sullo store.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from windows_os_api.api.rest.deps import get_concurrency_gate, reset_limiter
from windows_os_api.core.events.bus import get_event_bus
from windows_os_api.core.runtime.app import create_app
from windows_os_api.core.runtime.config import get_settings
from windows_os_api.core.security.rate_limit import ConcurrencyGate

LOCK_NAME = ".winos-instance.lock"


@pytest.fixture
def runtime_env(tmp_path, monkeypatch):
    """Store isolato + limiter pulito: ogni test parte da un processo vergine."""
    store = tmp_path / "adapters"
    store.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(store))
    monkeypatch.setenv("WINOS_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    get_settings.cache_clear()
    reset_limiter()
    yield store
    reset_limiter()
    get_settings.cache_clear()


# --------------------------------------------------------------------------
# teardown del ciclo: nessun superstite
# --------------------------------------------------------------------------


def test_new_cycle_does_not_inherit_the_previous_cycle_bus(runtime_env):
    """La history del ciclo 1 non deve essere visibile al ciclo 2."""
    with TestClient(create_app()) as client:
        bus = get_event_bus()
        bus.publish_sync("system.probe", {"marker": "CICLO-1"}, provenance="system")
        assert client.get("/v1/live").status_code == 200
        assert len(bus.history(1000)) == 1
        first = bus

    with TestClient(create_app()) as client:
        bus = get_event_bus()
        assert client.get("/v1/live").status_code == 200
        assert bus is not first, "il ciclo 2 riusa il bus del ciclo 1"
        inherited = [
            e for e in bus.history(1000) if (e.payload or {}).get("marker") == "CICLO-1"
        ]
        assert inherited == [], f"il ciclo 2 eredita eventi del ciclo 1: {inherited}"
        assert bus.stats().subscribers == 0


def test_a_subscriber_of_the_previous_cycle_stops_at_teardown(runtime_env):
    """Il caso grave: un client WS del ciclo 1 che continua a leggere il ciclo 2."""
    received: list[str] = []
    subscribed = threading.Event()

    async def consume() -> None:
        bus = get_event_bus()
        agen = bus.subscribe()
        subscribed.set()
        async for event in agen:
            received.append((event.payload or {}).get("marker", ""))

    def run_loop() -> None:
        asyncio.new_event_loop().run_until_complete(consume())

    worker = threading.Thread(target=run_loop, daemon=True)

    with TestClient(create_app()) as client:
        assert client.get("/v1/live").status_code == 200
        worker.start()
        assert subscribed.wait(timeout=5)
        time.sleep(0.2)
        get_event_bus().publish_sync(
            "system.probe", {"marker": "C1"}, provenance="system"
        )
        time.sleep(0.2)

    worker.join(timeout=5)
    assert not worker.is_alive(), "il subscriber del ciclo 1 e' sopravvissuto al teardown"

    with TestClient(create_app()):
        get_event_bus().publish_sync(
            "system.probe", {"marker": "C2"}, provenance="system"
        )
        time.sleep(0.3)

    assert "C1" in received
    assert "C2" not in received, "il subscriber del ciclo 1 legge eventi del ciclo 2"


# --------------------------------------------------------------------------
# quota per principal/app
# --------------------------------------------------------------------------


def test_one_principal_cannot_spend_the_whole_process_budget():
    gate = ConcurrencyGate(limit=4, per_principal_limit=2)
    assert [gate.try_acquire("app-a") for _ in range(2)] == [True, True]
    assert gate.try_acquire("app-a") is False, "quota per principal non applicata"
    assert gate.try_acquire("app-b") is True, "un principal ha affamato gli altri"
    assert gate.in_flight_for("app-a") == 2
    assert gate.in_flight_for("app-b") == 1


def test_the_global_cap_still_applies_across_principals():
    gate = ConcurrencyGate(limit=2, per_principal_limit=2)
    assert gate.try_acquire("app-a") is True
    assert gate.try_acquire("app-b") is True
    assert gate.try_acquire("app-c") is False, "il tetto globale N013 e' saltato"


def test_releasing_frees_both_budgets_and_forgets_the_principal():
    gate = ConcurrencyGate(limit=4, per_principal_limit=1)
    assert gate.try_acquire("app-a") is True
    gate.release("app-a")
    assert gate.in_flight == 0
    assert gate.in_flight_for("app-a") == 0
    # Nessuna crescita illimitata: un principal a zero non resta in memoria.
    assert "app-a" not in gate.principals()
    assert gate.try_acquire("app-a") is True


def test_release_of_an_unknown_principal_does_not_go_negative():
    gate = ConcurrencyGate(limit=2, per_principal_limit=1)
    gate.release("mai-acquisito")
    assert gate.in_flight == 0
    assert gate.in_flight_for("mai-acquisito") == 0


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_a_missing_principal_falls_back_to_one_shared_bucket(bad):
    gate = ConcurrencyGate(limit=4, per_principal_limit=1)
    assert gate.try_acquire(bad) is True
    assert gate.try_acquire(bad) is False, "identita' assente non deve aggirare la quota"


def test_per_principal_budget_is_wired_into_the_http_path(runtime_env, monkeypatch):
    """Due chiavi diverse: la rumorosa viene frenata, l'altra passa."""
    monkeypatch.setenv("WINOS_MAX_CONCURRENT_PER_PRINCIPAL", "1")
    get_settings.cache_clear()
    reset_limiter()

    app = create_app()
    hold = asyncio.Event()

    @app.get("/__n046_slow")
    async def slow():  # pragma: no cover - esercitata via ASGI
        await hold.wait()
        return {"ok": True}

    async def scenario():
        gate = get_concurrency_gate(get_settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            noisy = asyncio.create_task(
                client.get("/__n046_slow", headers={"x-api-key": "key-aaaa-1111"})
            )
            await asyncio.sleep(0.1)
            assert gate.in_flight == 1

            try:
                # Senza quota per principal questa richiesta entra nella rotta e
                # resta appesa: il timeout la trasforma in un rosso, non in un hang.
                same = await asyncio.wait_for(
                    client.get("/__n046_slow", headers={"x-api-key": "key-aaaa-1111"}),
                    timeout=3,
                )
            except asyncio.TimeoutError:
                pytest.fail(
                    "la seconda richiesta dello stesso principal non e' stata frenata: "
                    "e' entrata nella rotta"
                )
            assert same.status_code == 429

            other = asyncio.create_task(
                client.get("/__n046_slow", headers={"x-api-key": "key-bbbb-2222"})
            )
            await asyncio.sleep(0.1)
            assert gate.in_flight == 2, "un principal diverso e' stato bloccato dalla quota altrui"

            hold.set()
            done = await asyncio.wait_for(asyncio.gather(noisy, other), timeout=10)
            assert [r.status_code for r in done] == [200, 200]
            await asyncio.sleep(0.05)
            assert gate.in_flight == 0

    asyncio.run(scenario())


def test_two_keys_sharing_the_first_eight_characters_are_distinct_principals():
    """Il rate limiter N013 tronca a 8 caratteri; il budget no."""
    from windows_os_api.core.security.rate_limit import principal_key

    a = principal_key("127.0.0.1", "winoskey-AAAAAAAAAAAA")
    b = principal_key("127.0.0.1", "winoskey-BBBBBBBBBBBB")
    assert a != b
    assert "winoskey" not in a and "AAAA" not in a, "la chiave non deve comparire in chiaro"


def test_cancelled_request_releases_the_budget(runtime_env):
    """Regressione: se il rilascio esce dal `finally`, il budget si esaurisce."""
    reset_limiter()
    app = create_app()
    hold = asyncio.Event()

    @app.get("/__n046_hang")
    async def hang():  # pragma: no cover - esercitata via ASGI
        await hold.wait()
        return {"ok": True}

    async def scenario():
        gate = get_concurrency_gate(get_settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            task = asyncio.create_task(client.get("/__n046_hang"))
            await asyncio.sleep(0.15)
            assert gate.in_flight == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.1)
            assert gate.in_flight == 0, "una richiesta cancellata ha trattenuto il budget"
            hold.set()

    asyncio.run(scenario())


def test_the_per_principal_budget_is_observable(runtime_env):
    """Un principal che satura la sua quota deve vedersi nelle metriche."""
    from windows_os_api.observability.metrics import collect_operational

    settings = get_settings()
    gate = get_concurrency_gate(settings)
    assert gate.try_acquire("key:probe") is True
    try:
        snapshot = collect_operational()
        assert snapshot["http.inflight"] == 1.0
        assert snapshot["http.inflight_principals"] == 1.0
        assert snapshot["http.inflight_per_principal_limit"] == float(
            settings.max_concurrent_per_principal
        )
    finally:
        gate.release("key:probe")
    assert collect_operational()["http.inflight_principals"] == 0.0


# --------------------------------------------------------------------------
# due istanze sullo stesso store
# --------------------------------------------------------------------------


def test_a_second_instance_on_the_same_store_is_refused(runtime_env):
    with TestClient(create_app()):
        with pytest.raises(RuntimeError) as err:
            with TestClient(create_app()):
                pass
        assert "istanza" in str(err.value).lower() or "instance" in str(err.value).lower()


def test_the_store_is_usable_again_after_teardown(runtime_env):
    with TestClient(create_app()) as first:
        assert first.get("/v1/live").status_code == 200
    # Teardown completato: una nuova istanza deve partire e servire.
    with TestClient(create_app()) as second:
        assert second.get("/v1/live").status_code == 200
    assert not (runtime_env / LOCK_NAME).exists(), "lock non rilasciato al teardown"


def test_a_lock_left_by_a_dead_process_is_reclaimed(runtime_env):
    stale = {"pid": 2147483646, "host": "altro-host", "started_at": 1.0}
    (runtime_env / LOCK_NAME).write_text(json.dumps(stale), encoding="utf-8")
    with TestClient(create_app()) as client:
        assert client.get("/v1/live").status_code == 200
        holder = json.loads((runtime_env / LOCK_NAME).read_text(encoding="utf-8"))
        assert holder["pid"] == os.getpid()


@pytest.mark.parametrize("junk", ["", "   ", "{non json", '{"pid": "abc"}', "[]"])
def test_a_malformed_lock_file_is_reclaimed_not_trusted(runtime_env, junk):
    (runtime_env / LOCK_NAME).write_text(junk, encoding="utf-8")
    with TestClient(create_app()) as client:
        assert client.get("/v1/live").status_code == 200


def test_a_clock_jump_does_not_release_a_live_lock(runtime_env, monkeypatch):
    """La liveness si decide sul PID, mai sull'orologio (H63-N046 clock jump)."""
    with TestClient(create_app()):
        lock_path = runtime_env / LOCK_NAME
        held = json.loads(lock_path.read_text(encoding="utf-8"))
        assert held["pid"] == os.getpid()
        # Orologio spostato molto avanti: il lock resta valido.
        monkeypatch.setattr(time, "time", lambda: held["started_at"] + 10_000_000)
        with pytest.raises(RuntimeError):
            with TestClient(create_app()):
                pass
        # ...e molto indietro.
        monkeypatch.setattr(time, "time", lambda: 0.0)
        with pytest.raises(RuntimeError):
            with TestClient(create_app()):
                pass


def test_a_failed_start_does_not_leave_the_lock_behind(runtime_env, monkeypatch):
    """START fallito => nessuna sessione parzialmente attiva, lock compreso."""
    import windows_os_api.core.runtime.app as app_module

    class Boom(RuntimeError):
        pass

    class RefusingAudit:
        def log(self, *args, **kwargs):
            raise Boom("audit non disponibile (N042 fail-closed)")

    monkeypatch.setattr(app_module, "get_audit_logger", lambda: RefusingAudit())
    with pytest.raises(Boom):
        with TestClient(create_app()):
            pass
    assert not (runtime_env / LOCK_NAME).exists(), "avvio fallito ha lasciato il lock"

    # E il runtime successivo parte davvero.
    monkeypatch.undo()
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        assert client.get("/v1/live").status_code == 200


def test_only_one_of_many_concurrent_acquirers_wins(runtime_env):
    """Il caso che i test sequenziali non vedevano: due processi insieme.

    `read_holder()` seguito da una scrittura lascia una finestra fra il
    controllo e l'effetto: chi legge «libero» nello stesso istante scrive tutti,
    e ognuno crede di avere il lock. Provato con 12 processi reali: ne
    acquisivano 3.
    """
    from windows_os_api.core.runtime.instance_lock import (
        InstanceLock,
        InstanceLockTaken,
    )

    winners: list[int] = []
    errors: list[BaseException] = []
    start = threading.Barrier(12)

    def contend(n: int) -> None:
        try:
            start.wait(timeout=10)
            InstanceLock(runtime_env).acquire()
            winners.append(n)
        except InstanceLockTaken:
            pass
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
        assert not t.is_alive()

    assert not errors, f"un acquirente e' morto invece di essere rifiutato: {errors}"
    assert len(winners) == 1, f"lock acquisito da {len(winners)} contendenti: {winners}"


def test_a_refused_acquirer_fails_cleanly_not_with_a_stray_oserror(runtime_env):
    """Il rifiuto deve essere InstanceLockTaken, non un FileNotFoundError.

    Con un file temporaneo condiviso fra i contendenti, il `replace` di uno
    faceva sparire il tmp dell'altro: nella riproduzione a 12 processi, 9
    morivano con FileNotFoundError non gestito — in produzione avrebbero fatto
    esplodere il lifespan invece di essere respinti.
    """
    from windows_os_api.core.runtime.instance_lock import (
        InstanceLock,
        InstanceLockTaken,
    )

    held = InstanceLock(runtime_env)
    held.acquire()
    try:
        with pytest.raises(InstanceLockTaken):
            InstanceLock(runtime_env).acquire()
    finally:
        held.release()


def test_the_lock_is_not_stolen_from_a_live_holder(runtime_env):
    with TestClient(create_app()):
        holder = json.loads((runtime_env / LOCK_NAME).read_text(encoding="utf-8"))
        assert holder["pid"] == os.getpid()
        try:
            with TestClient(create_app()):
                pass
        except RuntimeError:
            pass
        after = json.loads((runtime_env / LOCK_NAME).read_text(encoding="utf-8"))
        assert after == holder, "un'istanza rifiutata ha comunque riscritto il lock"
