"""N002 — fixture isolation + fake/live separation (H63-N002 / #67).

Two inverse-order cases:
1) no adapter / subscriber / policy / counter inherited across resets
2) live fixture rejects FakeBackend (fail-closed)

Families Q00/Q15. Unit-level contract; not the installed-product harness (N003).
"""
from __future__ import annotations

import pytest

from windows_os_api.apps.adapters.engine import reset_adapters, list_adapters
from windows_os_api.apps.adapters.store import store_dir, clear_adapter_store
from windows_os_api.apps.sandbox.permissions import (
    SandboxPolicy,
    get_policy,
    reset_policies,
    set_policy,
)
from windows_os_api.apps.workflows.recorder import reset_workflows
from windows_os_api.backends.factory import reset_backend
from windows_os_api.backends.fake import FakeBackend
from windows_os_api.core.events.bus import get_event_bus, reset_event_bus
from windows_os_api.core.runtime.config import get_settings
from windows_os_api.core.security.audit import reset_audit_logger
from windows_os_api.observability.metrics import get_metrics, reset_metrics
from windows_os_api.api.rest.deps import get_limiter, reset_limiter


def _conftest_style_partial_reset() -> None:
    """Mirrors pre-N002 tmp_sandbox teardown (no bus/metrics/limiter/store)."""
    get_settings.cache_clear()
    reset_backend()
    reset_adapters()
    reset_workflows()
    reset_policies()
    reset_audit_logger()


def _full_isolation_reset() -> None:
    """Reset surface that N002 wires into fixtures."""
    _conftest_style_partial_reset()
    reset_event_bus()
    reset_metrics()
    reset_limiter()
    clear_adapter_store()


def test_h63_n002_no_state_inherited_across_full_reset(tmp_path, monkeypatch):
    """Case 1 (inverse order first): adapter/subscriber/policy/counter not inherited."""
    store = tmp_path / "adapters"
    store.mkdir()
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(store))
    monkeypatch.setenv("WINOS_BACKEND", "fake")
    get_settings.cache_clear()

    bus = get_event_bus()
    bus.publish_sync("n002.seed", {"k": 1})
    assert any(e.type == "n002.seed" for e in bus.history())

    metrics = get_metrics()
    metrics.incr("n002_counter", 7)
    assert metrics.snapshot()["counters"].get("n002_counter") == 7

    lim = get_limiter(get_settings())
    for _ in range(3):
        lim.allow("n002-subject")
    assert lim.remaining("n002-subject") < lim.limit

    set_policy(SandboxPolicy(app_id="n002-app", allowed_actions={"click"}))
    assert "click" in get_policy("n002-app").allowed_actions

    marker = store_dir() / "n002_leak.json"
    marker.write_text('{"leak": true}', encoding="utf-8")
    assert marker.exists()

    _full_isolation_reset()

    assert get_event_bus().history() == []
    assert get_metrics().snapshot()["counters"].get("n002_counter", 0) == 0

    lim2 = get_limiter(get_settings())
    assert lim2.remaining("n002-subject") == lim2.limit

    assert get_policy("n002-app").allowed_actions == set()
    assert list_adapters() == []
    assert not marker.exists(), "adapter store marker must be wiped"
    assert list(store_dir().glob("*.json")) == []


def test_h63_n002_live_fixture_rejects_fake_backend(tmp_path, monkeypatch):
    """Case 2: live fixture fail-closed when backend resolves to FakeBackend."""
    import tests.conftest as root_conftest

    assert hasattr(root_conftest, "live_backend")
    assert hasattr(root_conftest, "ensure_live_backend")
    assert hasattr(root_conftest, "assert_not_fake_backend")

    with pytest.raises(RuntimeError, match="rejects FakeBackend"):
        root_conftest.assert_not_fake_backend(FakeBackend(str(tmp_path)))

    monkeypatch.setenv("WINOS_BACKEND", "fake")
    monkeypatch.setenv("WINOS_ALLOW_FAKE_FALLBACK", "true")
    monkeypatch.setenv("WINOS_SANDBOX_ROOT", str(tmp_path / "sandbox"))
    (tmp_path / "sandbox").mkdir(exist_ok=True)
    get_settings.cache_clear()
    reset_backend()

    with pytest.raises(RuntimeError, match="rejects FakeBackend"):
        root_conftest.ensure_live_backend()


def test_reset_helpers_exported():
    """Reset helpers must exist for bus / metrics / limiter / adapter store."""
    from windows_os_api.core.events import bus as bus_mod
    from windows_os_api.observability import metrics as metrics_mod
    from windows_os_api.api.rest import deps as deps_mod
    from windows_os_api.apps.adapters import store as store_mod

    assert callable(getattr(bus_mod, "reset_event_bus"))
    assert callable(getattr(metrics_mod, "reset_metrics"))
    assert callable(getattr(deps_mod, "reset_limiter"))
    assert callable(getattr(store_mod, "clear_adapter_store"))
