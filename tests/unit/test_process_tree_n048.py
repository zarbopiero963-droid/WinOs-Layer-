"""N048 / H63-N048 — process tree, restart, teardown (Q04/Q12).

Coverage refs: R05 W007 W008 L007 L008 G01 G21.
Installed W/L: MANUAL_ONLY (never claim PASS here).

Contratto: inventario parent/children/modules/threads/handles/resources;
restart e terminate limitati ai processi posseduti; zero figli residui dopo
teardown; terminate estraneo ancora negato (N047 invariato).
"""
from __future__ import annotations

import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from windows_os_api.backends.factory import get_backend, reset_backend
from windows_os_api.core.runtime.config import get_settings
from windows_os_api.os.processes import identity as ident
from windows_os_api.os.processes import service as procs
from windows_os_api.os.processes import tree as proc_tree


@pytest.fixture
def fake_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("WINOS_BACKEND", "fake")
    get_settings.cache_clear()
    reset_backend()
    ident.reset_registry()
    yield get_backend()
    ident.reset_registry()
    reset_backend()
    get_settings.cache_clear()


@pytest.fixture
def linux_runtime(monkeypatch):
    if os.name == "nt":
        pytest.skip("probe reale Linux")
    monkeypatch.setenv("WINOS_BACKEND", "linux")
    monkeypatch.setenv("WINOS_ALLOW_FAKE_FALLBACK", "false")
    get_settings.cache_clear()
    reset_backend()
    ident.reset_registry()
    yield
    ident.reset_registry()
    reset_backend()
    get_settings.cache_clear()


def _start_owned(fake_runtime, *, args: list[str] | None = None):
    """Avvia via service: policy + registro. Su fake l'exe deve esistere sul FS."""
    started = procs.start_process(sys.executable, args or ["-V"])
    assert started.get("ok", True), started
    assert started.get("identity_recorded") is True
    assert "argv" in started
    return started


# ---------------------------------------------------------------------------
# Inventario
# ---------------------------------------------------------------------------


def test_inspect_exposes_parent_children_threads_modules_handles_resources(fake_runtime):
    started = _start_owned(fake_runtime)
    child = fake_runtime.attach_child_process(started["pid"], name="helper.exe")

    info = procs.inspect_process(started["pid"])

    assert info["ok"] is True
    assert any(c["pid"] == child["pid"] for c in info["children"])
    assert "threads" in info and info["threads"]["available"] is True
    assert "modules" in info and info["modules"]["available"] is True
    assert "handles" in info and info["handles"]["available"] is True
    assert "resources" in info
    assert info["owned_by_runtime"] is True


def test_process_tree_is_nested_and_counts_descendants(fake_runtime):
    started = _start_owned(fake_runtime)
    child = fake_runtime.attach_child_process(started["pid"], name="helper.exe")
    grand = fake_runtime.attach_child_process(child["pid"], name="grand.exe")

    tree = procs.get_process_tree(started["pid"])

    assert tree["ok"] is True
    assert tree["descendant_count"] == 2
    child_pids = {c["pid"] for c in tree["tree"]["children"]}
    assert child["pid"] in child_pids
    # nipote sotto il figlio
    nested = tree["tree"]["children"][0]["children"]
    assert any(n["pid"] == grand["pid"] for n in nested)


def test_inspect_of_unknown_pid_is_not_a_fake_success(fake_runtime):
    out = procs.inspect_process(9_999_999)
    assert out["ok"] is False
    assert out["code"] == proc_tree.PROCESS_TREE_NOT_FOUND


# ---------------------------------------------------------------------------
# Teardown: zero figli residui
# ---------------------------------------------------------------------------


def test_terminate_owned_root_also_tears_down_children(fake_runtime):
    """Il falso successo di Phase 0: ok sul padre con figlio ancora vivo."""
    started = _start_owned(fake_runtime)
    child = fake_runtime.attach_child_process(started["pid"], name="orphan.exe")

    out = procs.terminate_process(started["pid"])

    assert out.get("ok") is True, out
    assert child["pid"] in out.get("terminated_children", [])
    assert out.get("residual_children") == []
    assert fake_runtime.get_process(child["pid"])["status"] == "terminated"
    assert ident.get_registry().get(started["pid"]) is None


def test_stranger_terminate_still_denied_without_expectation(fake_runtime):
    """N047 resta chiuso: un PID non nostro non si termina (né il suo albero)."""
    stranger = fake_runtime.start_process(sys.executable, ["-V"])
    child = fake_runtime.attach_child_process(stranger["pid"], name="stranger-child.exe")

    out = procs.terminate_process(stranger["pid"])

    assert out["ok"] is False
    assert out["code"] == ident.PROCESS_IDENTITY_REQUIRED
    assert fake_runtime.get_process(stranger["pid"])["status"] == "running"
    assert fake_runtime.get_process(child["pid"])["status"] == "running"


# ---------------------------------------------------------------------------
# Restart
# ---------------------------------------------------------------------------


def test_restart_owned_process_relaunches_same_exe_and_argv(fake_runtime):
    started = _start_owned(fake_runtime, args=["-V"])
    old_pid = started["pid"]

    out = procs.restart_process(old_pid)

    assert out.get("ok") is True, out
    assert out["old_pid"] == old_pid
    assert out["pid"] != old_pid
    assert out["exe"] == started["exe"]
    assert out["argv"] == ["-V"]
    assert ident.get_registry().get(old_pid) is None
    assert ident.get_registry().get(out["pid"]) is not None


def test_restart_of_unowned_process_is_refused(fake_runtime):
    stranger = fake_runtime.start_process(sys.executable, ["-V"])

    out = procs.restart_process(stranger["pid"])

    assert out["ok"] is False
    assert out["code"] == proc_tree.PROCESS_RESTART_NOT_OWNED
    assert fake_runtime.get_process(stranger["pid"])["status"] == "running"


def test_restart_without_recorded_exe_is_refused_not_invented(fake_runtime):
    started = _start_owned(fake_runtime, args=["-V"])
    pid = started["pid"]
    recorded = ident.get_registry().get(pid)
    assert recorded is not None
    # Record legacy senza exe: inventare il comando sarebbe un bug. argv vuoto
    # invece e' legittimo (avvio senza argomenti) e non deve bloccare il restart.
    ident.get_registry().record(
        ident.ProcessIdentity(
            pid=pid,
            create_time=recorded.create_time,
            exe=None,
            owner=recorded.owner,
            argv=recorded.argv,
        )
    )

    out = procs.restart_process(pid)

    assert out["ok"] is False
    assert out["code"] == proc_tree.PROCESS_RESTART_UNKNOWN


def test_restart_tears_down_children_before_relaunch(fake_runtime):
    started = _start_owned(fake_runtime, args=["-V"])
    child = fake_runtime.attach_child_process(started["pid"], name="worker.exe")

    out = procs.restart_process(started["pid"])

    assert out.get("ok") is True, out
    assert child["pid"] in out["teardown"]["terminated_children"]
    assert out["teardown"]["residual_children"] == []
    assert fake_runtime.get_process(child["pid"])["status"] == "terminated"


# ---------------------------------------------------------------------------
# Probe reale Linux — il falso successo di Phase 0 non deve ripetersi
# ---------------------------------------------------------------------------


def test_real_multiprocess_teardown_leaves_no_orphan_children(linux_runtime, tmp_path):
    psutil = pytest.importorskip("psutil")
    script = tmp_path / "n048_multiproc.py"
    # tmp_path e' dell'utente del test, non world-writable nel senso della policy
    # (la policy vieta /tmp, %TEMP%, Downloads — pytest tmp di solito e' sotto
    # /tmp/pytest-of-* : puo' essere rifiutato. Mettiamo lo script sotto il repo.
    repo_script = Path("sandbox") / "_n048_multiproc_test.py"
    repo_script.parent.mkdir(exist_ok=True)
    repo_script.write_text(
        textwrap.dedent(
            """\
            import subprocess, sys, time
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            print(child.pid, flush=True)
            time.sleep(60)
            """
        ),
        encoding="utf-8",
    )
    repo_script.chmod(0o755)
    try:
        # Se la policy rifiuta sandbox/, usiamo sys.executable -c e' vietato:
        # quindi lo script deve stare fuori dalle aree proibite. Preferiamo
        # un path sotto /workspace (non /tmp).
        started = procs.start_process(sys.executable, [str(repo_script.resolve())])
        if not started.get("ok", True) and started.get("code") == "EXECUTABLE_LOCATION_FORBIDDEN":
            pytest.skip(f"policy rifiuta path sandbox: {started}")
        assert started.get("ok", True), started
        parent_pid = started["pid"]
        deadline = time.time() + 3
        children: list[int] = []
        while time.time() < deadline:
            try:
                children = [c.pid for c in psutil.Process(parent_pid).children(recursive=True)]
            except psutil.Error:
                children = []
            if children:
                break
            time.sleep(0.05)
        assert children, "il processo multiproc non ha generato figli"

        out = procs.terminate_process(parent_pid)
        assert out.get("ok") is True, out
        assert out.get("residual_children") == []

        time.sleep(0.3)
        alive = []
        for cpid in children:
            try:
                p = psutil.Process(cpid)
                if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                    alive.append(cpid)
            except psutil.Error:
                pass
        assert alive == [], f"orfani ancora vivi dopo teardown: {alive}"
    finally:
        repo_script.unlink(missing_ok=True)
        # cleanup di emergenza
        try:
            if started.get("pid"):
                p = psutil.Process(started["pid"])
                for c in p.children(recursive=True):
                    c.kill()
                p.kill()
        except Exception:
            pass
