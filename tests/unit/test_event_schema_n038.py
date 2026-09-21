"""N038 — Event schema allowlist + redaction before fan-out (H63-N038).

Case: unknown type ``admin.granted`` and secret/oversize payloads must be
rejected or redacted *before* any subscriber / history entry.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from windows_os_api.core.events.bus import Event, EventBus, get_event_bus, reset_event_bus
from windows_os_api.core.events.schema import (
    ALLOWED_EVENT_TYPES,
    MAX_PAYLOAD_BYTES,
    SECRET_KEYS,
    redact_secrets,
    validate_and_sanitize,
)


@pytest.fixture(autouse=True)
def _isolated_bus():
    reset_event_bus()
    yield
    reset_event_bus()


def test_admin_granted_unknown_type_rejected_before_history():
    bus = EventBus()
    ev = bus.publish_sync("admin.granted", {"role": "root", "user": "eve"})
    assert ev is None
    assert bus.last_reject_reason == "type_not_allowed"
    assert bus.history() == []


def test_admin_granted_never_reaches_subscriber():
    async def _run():
        bus = EventBus()
        received: list[Event] = []

        async def consumer():
            async for ev in bus.subscribe():
                received.append(ev)
                break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.02)
        assert bus.publish_sync("admin.granted", {"role": "root"}) is None
        # Allowlisted probe so the subscriber can exit cleanly
        ok = bus.publish_sync("system.probe", {"ok": True}, provenance="system")
        assert ok is not None
        await asyncio.wait_for(task, timeout=2.0)
        assert [e.type for e in received] == ["system.probe"]
        assert all(e.type != "admin.granted" for e in received)

    asyncio.run(_run())


def test_secret_keys_redacted_before_history_and_subscriber():
    async def _run():
        bus = EventBus()
        dirty = {
            "goal": "do thing",
            "api_key": "sk-live-secret",
            "nested": {"password": "hunter2", "ok": 1, "token": "t"},
        }
        received: list[Event] = []

        async def consumer():
            async for ev in bus.subscribe():
                received.append(ev)
                break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.02)
        ev = bus.publish_sync("system.probe", dirty, provenance="system")
        assert ev is not None
        assert "api_key" not in ev.payload
        assert "password" not in ev.payload.get("nested", {})
        assert "token" not in ev.payload.get("nested", {})
        assert ev.payload["nested"]["ok"] == 1
        hist = bus.history()
        assert len(hist) == 1
        blob = json.dumps(hist[0].payload)
        for banned in ("sk-live-secret", "hunter2", "api_key", "password", "token"):
            assert banned not in blob, blob
        await asyncio.wait_for(task, timeout=2.0)
        assert received[0].payload == hist[0].payload

    asyncio.run(_run())


def test_oversize_payload_rejected_before_fan_out():
    bus = EventBus()
    huge = {"blob": "x" * (MAX_PAYLOAD_BYTES + 64)}
    ev = bus.publish_sync("system.probe", huge, provenance="system")
    assert ev is None
    assert bus.last_reject_reason == "payload_too_large"
    assert bus.history() == []


def test_unknown_provenance_rejected():
    bus = EventBus()
    ev = bus.publish_sync("system.probe", {"ok": 1}, provenance="forged-external")
    assert ev is None
    assert bus.last_reject_reason == "provenance_not_allowed"


def test_async_publish_also_gates():
    async def _run():
        bus = EventBus()
        bad = await bus.publish(Event(type="admin.granted", payload={"x": 1}))
        assert bad is None
        good = await bus.publish(
            Event(type="system.probe", payload={"api_key": "nope", "n": 2}, provenance="system")
        )
        assert good is not None
        assert "api_key" not in good.payload
        assert good.payload["n"] == 2
        assert [e.type for e in bus.history()] == ["system.probe"]

    asyncio.run(_run())


def test_validate_and_sanitize_unit_contract():
    clean, reason = validate_and_sanitize("admin.granted", {"a": 1})
    assert clean is None and reason == "type_not_allowed"
    clean, reason = validate_and_sanitize(
        "system.probe", {"secret": "x", "keep": True}, provenance="system"
    )
    assert reason is None
    assert clean == {"keep": True}
    assert "system.probe" in ALLOWED_EVENT_TYPES
    assert "api_key" in SECRET_KEYS
    assert redact_secrets({"token": "t", "a": 1}) == {"a": 1}


def test_agent_goal_proposed_before_gate_not_as_executed(tmp_path, monkeypatch):
    """ComputerAgent must emit proposed, then executed|denied — never bare agent.goal."""
    monkeypatch.setenv("WINOS_BACKEND", "fake")
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(tmp_path / "adapters"))
    from windows_os_api.core.runtime.config import get_settings

    get_settings.cache_clear()
    reset_event_bus()

    from windows_os_api.apps.agent.computer import ComputerAgent

    agent = ComputerAgent("contoso-crm")
    agent.run("new customer")  # typically denied / planned
    types = [e.type for e in get_event_bus().history()]
    assert "agent.goal" not in types
    assert types[0] == "agent.goal.proposed"
    assert "agent.goal.executed" in types or "agent.goal.denied" in types
    assert types.count("agent.goal.proposed") == 1


def test_x_api_key_hyphen_alias_is_redacted():
    """Audit H63-N038: alias x-api-key must not survive fan-out."""
    clean, reason = validate_and_sanitize(
        "system.probe",
        {"x-api-key": "synthetic-secret", "password": "p", "ok": True},
        provenance="system",
    )
    assert reason is None
    assert clean is not None
    assert "x-api-key" not in clean
    assert "password" not in clean
    assert clean.get("ok") is True
    # underscore form already allowlisted; hyphen must match too
    assert "x-api-key" in {k.replace("_", "-") for k in SECRET_KEYS} or "x-api-key" in SECRET_KEYS
