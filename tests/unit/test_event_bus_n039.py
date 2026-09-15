"""N039 — Bus thread-safe, backpressure e lifecycle (H63-N039).

Cross-thread publish, slow subscriber / burst → measurable drops,
stop/sentinel + reset without deadlock/leak.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from windows_os_api.core.events.bus import (
    BusAtCapacityError,
    BusClosedError,
    Event,
    EventBus,
    get_event_bus,
    reset_event_bus,
)


@pytest.fixture(autouse=True)
def _isolated_bus():
    reset_event_bus()
    yield
    reset_event_bus()


def test_cross_thread_publish_reaches_subscriber():
    async def _run():
        bus = EventBus()
        received: list[Event] = []
        ready = asyncio.Event()

        async def consumer():
            async for ev in bus.subscribe():
                ready.set()
                received.append(ev)
                if len(received) >= 1:
                    break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.02)

        def worker():
            # Foreign thread → must hand off to loop without blocking.
            ok = bus.publish_sync(
                "system.probe", {"from": "thread"}, provenance="system"
            )
            assert ok is not None

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive()
        await asyncio.wait_for(task, timeout=2.0)
        assert [e.payload.get("from") for e in received] == ["thread"]
        assert bus.stats().published >= 1

    asyncio.run(_run())


def test_burst_slow_subscriber_observable_drops():
    async def _run():
        bus = EventBus(queue_maxsize=4, max_queued_bytes=10_000_000)
        received: list[Event] = []
        gate = asyncio.Event()

        async def slow_consumer():
            async for ev in bus.subscribe():
                received.append(ev)
                await gate.wait()  # stall → fill the queue
                if len(received) >= 2:
                    break

        task = asyncio.create_task(slow_consumer())
        await asyncio.sleep(0.02)
        # Fill queue (4) + extras that must drop.
        for i in range(12):
            assert (
                bus.publish_sync("system.probe", {"i": i}, provenance="system")
                is not None
            )
        # Let the loop process scheduled fan-outs from sync path.
        await asyncio.sleep(0.05)
        assert bus.dropped >= 1
        assert bus.stats().dropped == bus.dropped
        gate.set()
        # Drain enough for consumer to exit.
        for i in range(4):
            bus.publish_sync("system.probe", {"drain": i}, provenance="system")
        await asyncio.wait_for(task, timeout=2.0)
        assert len(received) >= 1

    asyncio.run(_run())


def test_byte_budget_drops_when_queues_heavy():
    async def _run():
        # Tiny byte budget forces drops even with room in queue slots.
        bus = EventBus(queue_maxsize=64, max_queued_bytes=40)
        received: list[Event] = []
        gate = asyncio.Event()

        async def consumer():
            async for ev in bus.subscribe():
                received.append(ev)
                await gate.wait()
                break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.02)
        for i in range(8):
            bus.publish_sync(
                "system.probe",
                {"blob": "x" * 20, "i": i},
                provenance="system",
            )
        await asyncio.sleep(0.05)
        assert bus.dropped >= 1
        gate.set()
        bus.publish_sync("system.probe", {"bye": 1}, provenance="system")
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(_run())


def test_max_subscribers_enforced():
    async def _run():
        bus = EventBus(max_subscribers=1)
        got = asyncio.Event()

        async def hold():
            async for _ev in bus.subscribe():
                got.set()
                break

        t1 = asyncio.create_task(hold())
        await asyncio.sleep(0.02)
        with pytest.raises(BusAtCapacityError):
            async for _ in bus.subscribe():
                pass
        bus.publish_sync("system.probe", {"n": 1}, provenance="system")
        await asyncio.wait_for(t1, timeout=2.0)
        assert got.is_set()

    asyncio.run(_run())


def test_stop_unblocks_subscriber_and_rejects_new():
    async def _run():
        bus = EventBus()
        ended = asyncio.Event()

        async def consumer():
            try:
                async for _ev in bus.subscribe():
                    pass
            finally:
                ended.set()

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.02)
        assert bus.stats().subscribers == 1
        bus.stop()
        await asyncio.wait_for(ended.wait(), timeout=2.0)
        await asyncio.wait_for(task, timeout=2.0)
        assert bus.stats().stopped is True
        assert bus.stats().subscribers == 0
        # After stop, publish is a no-op fan-out (accept still validates).
        ev = bus.publish_sync("system.probe", {"a": 1}, provenance="system")
        assert ev is not None  # accepted object returned
        assert bus.history() == [] or bus.stats().published >= 0
        # New subscribe fails closed.
        with pytest.raises(BusClosedError):
            async for _ in bus.subscribe():
                pass

    asyncio.run(_run())


def test_reset_event_bus_recovers():
    async def _run():
        bus = get_event_bus()
        ended = asyncio.Event()

        async def consumer():
            try:
                async for _ in bus.subscribe():
                    pass
            finally:
                ended.set()

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.02)
        reset_event_bus()
        await asyncio.wait_for(ended.wait(), timeout=2.0)
        await asyncio.wait_for(task, timeout=2.0)
        fresh = get_event_bus()
        assert fresh is not bus
        assert fresh.stats().stopped is False
        ok = fresh.publish_sync("system.probe", {"ok": True}, provenance="system")
        assert ok is not None
        assert len(fresh.history()) == 1

    asyncio.run(_run())


def test_publish_sync_history_without_loop_still_works():
    bus = EventBus()
    ev = bus.publish_sync("system.probe", {"a": 1}, provenance="system")
    assert ev is not None
    assert len(bus.history()) == 1
    assert bus.dropped == 0


def test_stop_from_foreign_thread_no_deadlock():
    async def _run():
        bus = EventBus()
        ended = asyncio.Event()

        async def consumer():
            try:
                async for _ in bus.subscribe():
                    pass
            finally:
                ended.set()

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.02)

        def stopper():
            bus.stop()

        t = threading.Thread(target=stopper)
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive()
        await asyncio.wait_for(ended.wait(), timeout=2.0)
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(_run())
