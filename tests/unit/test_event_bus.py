"""Event bus unit tests."""
import asyncio
from windows_os_api.core.events.bus import EventBus, Event

def test_publish_sync_history():
    bus = EventBus()
    ev = bus.publish_sync("test.event", {"a": 1})
    assert ev.type == "test.event"
    hist = bus.history(10)
    assert len(hist) == 1
    assert hist[0].payload["a"] == 1

def test_subscribe_receives():
    async def _run():
        bus = EventBus()
        async def producer():
            await asyncio.sleep(0.01)
            await bus.publish(Event(type="ping", payload={"n": 1}))
        task = asyncio.create_task(producer())
        got = None
        async for ev in bus.subscribe():
            got = ev
            break
        await task
        assert got is not None
        assert got.type == "ping"
    asyncio.run(_run())
