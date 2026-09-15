"""N045 / H63-N045 — lock order, verify/invoke/store consistency (Q12).

Coverage refs: R22 R26 R33 R49 G17 G19.
Installed W/L: MANUAL_ONLY (never claim PASS here).
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from pathlib import Path

import pytest

from windows_os_api.apps.adapters import store
from windows_os_api.apps.adapters import verification as verif
from windows_os_api.apps.adapters.engine import (
    create_adapter,
    get_adapter,
    invoke_action,
    load_persisted_adapters,
    reset_adapters,
    verify_and_record,
)
from windows_os_api.apps.adapters.lock_order import (
    LOCK_ORDER,
    action_content_fingerprint,
    verification_matches_action,
)
from windows_os_api.apps.sandbox.permissions import reset_policies
from windows_os_api.backends.factory import get_backend, reset_backend
from windows_os_api.core.runtime.config import get_settings

APP = "n045-lock-app"


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("WINOS_ADAPTER_STORE", str(tmp_path / "adapters"))
    get_settings.cache_clear()
    reset_backend()
    reset_adapters()
    reset_policies()
    yield create_adapter(APP, hwnd=1001)
    reset_policies()
    reset_adapters()


def _edit_name(adapter) -> str:
    return next(a.name for a in adapter.actions if a.control_type == "Edit")


def test_lock_order_levels_are_strictly_ascending():
    levels = [level for _name, level, _sym in LOCK_ORDER]
    assert levels == sorted(levels)
    assert levels == [1, 2, 3]


def test_fingerprint_changes_when_action_identity_changes(adapter):
    action = next(a for a in adapter.actions if a.control_type == "Edit")
    fp1 = action_content_fingerprint(action)
    action.automation_id = action.automation_id + "-mutated"
    assert action_content_fingerprint(action) != fp1


def test_verified_fp_mismatch_does_not_match(adapter):
    action = next(a for a in adapter.actions if a.control_type == "Edit")
    # Missing action_fp allowed in-memory; store migrates on load.
    assert verification_matches_action(
        {"state": "VERIFIED", "verification_id": "ver_x"}, action
    ) is True
    assert verification_matches_action({"state": "FAILED"}, action) is True
    bad = {
        "state": "VERIFIED",
        "verification_id": "ver_x",
        "action_fp": "0" * 32,
    }
    assert verification_matches_action(bad, action) is False


def test_concurrent_verify_and_invoke_serialize(adapter, monkeypatch):
    """External invoke must not interleave with an in-flight probe (H63-N045)."""
    from windows_os_api.apps.adapters import engine

    action = _edit_name(adapter)
    real_invoke = engine._invoke_action_locked
    state = {"active": 0, "max": 0}
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def slow_locked(ad, action_obj, app_id, action_name, params):
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        try:
            time.sleep(0.03)
            return real_invoke(ad, action_obj, app_id, action_name, params)
        finally:
            with lock:
                state["active"] -= 1

    monkeypatch.setattr(engine, "_invoke_action_locked", slow_locked)

    def do_verify():
        barrier.wait(timeout=5)
        return verif.verify_action(APP, action)

    def do_invoke():
        barrier.wait(timeout=5)
        return invoke_action(APP, action, {"value": "external"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_v = pool.submit(do_verify)
        f_i = pool.submit(do_invoke)
        verdict = f_v.result(timeout=30)
        inv = f_i.result(timeout=30)

    assert state["max"] == 1, state
    assert verdict["state"] in verif.VERIFICATION_STATES
    assert "ok" in inv


def test_two_verifies_plus_invoke_no_deadlock(adapter):
    name = _edit_name(adapter)
    start = threading.Barrier(3)
    errors: list[BaseException] = []

    def run_verify():
        try:
            start.wait(timeout=5)
            verify_and_record(APP, name)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def run_invoke():
        try:
            start.wait(timeout=5)
            invoke_action(APP, name, {"value": "n045-external"})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=run_verify),
        threading.Thread(target=run_verify),
        threading.Thread(target=run_invoke),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "deadlock"
    assert not errors, errors

    recorded = next(a for a in get_adapter(APP).actions if a.name == name)
    assert recorded.verification is not None
    assert recorded.verification.get("state") in verif.VERIFICATION_STATES
    if recorded.verification.get("state") == verif.VERIFIED:
        assert recorded.verification.get("action_fp") == action_content_fingerprint(
            recorded
        )

    manifests, skipped = store.load_all()
    assert not skipped, skipped
    disk = next(m for m in manifests if m["app_id"] == APP)
    disk_action = next(a for a in disk["actions"] if a["name"] == name)
    assert disk_action.get("verification") is not None


def test_threadpool_saturated_no_deadlock_or_corrupt_store(adapter):
    name = _edit_name(adapter)
    seed = verify_and_record(APP, name)
    assert seed.get("recorded") is True, seed

    def worker(i: int):
        if i % 3 == 0:
            return invoke_action(APP, name, {"value": f"pool-{i}"})
        if i % 3 == 1:
            return verify_and_record(APP, name)
        ad = get_adapter(APP)
        assert ad is not None
        return {"path": str(store.save(ad))}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(worker, i) for i in range(24)]
        done, not_done = wait(futures, timeout=60)
        assert not not_done, "possible deadlock under saturation"
        for f in done:
            f.result()

    directory = Path(os.environ["WINOS_ADAPTER_STORE"])
    for path in directory.glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw.get("manifest_version") == store.MANIFEST_VERSION


def test_verify_and_record_stamps_action_fp(adapter):
    action = _edit_name(adapter)
    out = verify_and_record(APP, action)
    assert out["ok"] is True, out
    target = next(a for a in get_adapter(APP).actions if a.name == action)
    fp = action_content_fingerprint(target)
    assert target.verification["action_fp"] == fp
    assert verification_matches_action(target.verification, target)


def test_action_fp_mismatch_demoted_on_load(adapter):
    action = _edit_name(adapter)
    assert verify_and_record(APP, action)["ok"] is True

    manifest_path = next(Path(os.environ["WINOS_ADAPTER_STORE"]).glob("*.json"))
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    for act in raw["actions"]:
        if act.get("name") == action:
            # Keep stamped action_fp from prior content; mutate identity → mismatch
            act["automation_id"] = "field.MUTATED"
            break
    manifest_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    reset_adapters()
    load_persisted_adapters()
    restored = next(a for a in get_adapter(APP).actions if a.name == action)
    assert restored.verification["state"] != "VERIFIED"
    assert restored.verification.get("code") == "ACTION_FP_MISMATCH"
    assert "verification_id" not in restored.verification


def test_store_lock_not_held_during_ui(adapter, monkeypatch):
    """Level-3 store lock must not be held across UI/backend work in verify."""
    action = _edit_name(adapter)
    held_during_ui: list[bool] = []
    backend = get_backend()
    real_get = backend.get_ui_tree

    def probe_ui(hwnd):
        got = store._io_lock.acquire(blocking=False)
        held_during_ui.append(not got)
        if got:
            store._io_lock.release()
        return real_get(hwnd)

    monkeypatch.setattr(backend, "get_ui_tree", probe_ui)
    verdict = verif.verify_action(APP, action)
    assert verdict["state"] in verif.VERIFICATION_STATES
    assert held_during_ui, "UI never called"
    assert all(not h for h in held_during_ui), held_during_ui


def test_midflight_action_mutation_refuses_verified(adapter, monkeypatch):
    action_name = _edit_name(adapter)
    target = next(a for a in get_adapter(APP).actions if a.name == action_name)
    real_verify = verif.verify_action

    def mutate_then_verify(app_id, name):
        target.automation_id = target.automation_id + "-CHANGED_MIDFLIGHT"
        return real_verify(app_id, name)

    monkeypatch.setattr(
        "windows_os_api.apps.adapters.verification.verify_action",
        mutate_then_verify,
    )

    out = verify_and_record(APP, action_name)
    assert out.get("recorded") is True, out
    assert out["verification"]["state"] == "FAILED"
    assert out["verification"].get("code") == "ACTION_FP_CHANGED"
    assert out.get("verification_id") is None


def test_lock_order_doc_forbids_store_during_ui():
    doc = Path("windows_os_api/apps/adapters/lock_order.md").read_text(encoding="utf-8")
    assert "improper" in doc.lower()
    assert "store._io_lock" in doc
    assert "_verification_lock" in doc
