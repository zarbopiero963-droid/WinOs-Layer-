"""N047 / H63-N047 — process identity e launch autorizzato (Q04).

Coverage refs: R05 R21 W007 W008 L007 L008 G08 G20 G21.
Installed W/L: MANUAL_ONLY (never claim PASS here).

Due contratti, un file: cosa può essere avviato (`exec_policy`) e chi è davvero
il processo che stiamo per terminare (`identity`).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

from windows_os_api.os.processes import identity as ident
from windows_os_api.os.processes import service as procs
from windows_os_api.os.processes.exec_policy import (
    ARGS_INVALID,
    EXECUTABLE_LOCATION_FORBIDDEN,
    EXECUTABLE_NOT_FOUND,
    EXECUTABLE_PATH_INVALID,
    INTERPRETER_INLINE_CODE_FORBIDDEN,
    MAX_ARGS,
    ProcessLaunchRejected,
    authorize,
)

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="percorsi POSIX")


@pytest.fixture(autouse=True)
def clean_registry():
    ident.reset_registry()
    yield
    ident.reset_registry()


def _a_real_program() -> str:
    """Un eseguibile che esiste davvero su entrambe le piattaforme."""
    return sys.executable


# ---------------------------------------------------------------------------
# PASS — l'avvio legittimo resta legittimo
# ---------------------------------------------------------------------------


def test_an_installed_program_is_authorized_and_resolved_to_an_absolute_path():
    exe, args = authorize(_a_real_program(), ["-V"])
    assert Path(exe).is_absolute()
    assert Path(exe).is_file()
    assert args == ["-V"]


def test_a_bare_name_is_resolved_once_here_not_at_spawn_time():
    """Il PATH si consulta al momento della decisione, non dello spawn (TOCTOU)."""
    name = "python3" if shutil.which("python3") else Path(sys.executable).name
    exe, _ = authorize(name, [])
    assert Path(exe).is_absolute()
    assert Path(exe).is_file()


def test_no_arguments_at_all_is_fine():
    exe, args = authorize(_a_real_program(), None)
    assert args == []
    assert Path(exe).is_file()


# ---------------------------------------------------------------------------
# BLOCK — le forme di lancio arbitrarie per costruzione
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["-c", "-C"])
def test_an_interpreter_with_inline_code_is_refused(flag):
    """Il falso successo riprodotto su main: `sh -c` rispondeva ok=True."""
    sh = shutil.which("sh") or shutil.which("bash")
    if not sh:
        pytest.skip("nessuna shell POSIX in questo ambiente")
    with pytest.raises(ProcessLaunchRejected) as err:
        authorize(sh, [flag, "sleep 30"])
    assert err.value.code == INTERPRETER_INLINE_CODE_FORBIDDEN


def test_the_python_interpreter_cannot_be_asked_to_eval_code():
    with pytest.raises(ProcessLaunchRejected) as err:
        authorize(sys.executable, ["-c", "print('pwned')"])
    assert err.value.code == INTERPRETER_INLINE_CODE_FORBIDDEN


def test_the_same_interpreter_without_inline_code_is_allowed():
    """Il divieto è sul codice inline, non sull'interprete in sé."""
    exe, args = authorize(sys.executable, ["-V"])
    assert Path(exe).is_file()
    assert args == ["-V"]


def test_a_binary_dropped_in_a_world_writable_directory_is_refused():
    tmp_exe = Path(tempfile.gettempdir()) / "winos_n047_probe"
    tmp_exe.write_text("#!/bin/sh\necho no\n", encoding="utf-8")
    try:
        tmp_exe.chmod(0o755)
        with pytest.raises(ProcessLaunchRejected) as err:
            authorize(str(tmp_exe), [])
        assert err.value.code == EXECUTABLE_LOCATION_FORBIDDEN
    finally:
        tmp_exe.unlink(missing_ok=True)


def test_an_unknown_command_is_refused_not_guessed():
    with pytest.raises(ProcessLaunchRejected) as err:
        authorize("tool.exe", ["a"])
    assert err.value.code == EXECUTABLE_NOT_FOUND


@POSIX_ONLY
@pytest.mark.parametrize(
    "path", ["./relative-tool", "sub/dir/tool", "/usr/bin/../bin/sh"]
)
def test_ambiguous_paths_are_refused(path):
    with pytest.raises(ProcessLaunchRejected) as err:
        authorize(path, [])
    assert err.value.code in {EXECUTABLE_PATH_INVALID, EXECUTABLE_NOT_FOUND}


# ---------------------------------------------------------------------------
# MALFORMED — input che non è nemmeno una richiesta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, "", "   ", 42, [], {"cmd": "x"}])
def test_a_command_that_is_not_a_command_is_refused(bad):
    with pytest.raises(ProcessLaunchRejected) as err:
        authorize(bad, [])
    assert err.value.code == EXECUTABLE_PATH_INVALID


def test_a_nul_byte_in_the_command_is_refused():
    with pytest.raises(ProcessLaunchRejected) as err:
        authorize("/bin/sh\x00--evil", [])
    assert err.value.code == EXECUTABLE_PATH_INVALID


@pytest.mark.parametrize("bad", ["not-a-list", 7, {"a": 1}])
def test_args_that_are_not_a_list_are_refused(bad):
    with pytest.raises(ProcessLaunchRejected) as err:
        authorize(_a_real_program(), bad)
    assert err.value.code == ARGS_INVALID


def test_a_non_string_argument_is_refused():
    with pytest.raises(ProcessLaunchRejected) as err:
        authorize(_a_real_program(), ["-V", 3])
    assert err.value.code == ARGS_INVALID


def test_a_nul_byte_in_an_argument_is_refused():
    """Il NUL tronca a livello di syscall: il gate leggerebbe altro dal kernel."""
    with pytest.raises(ProcessLaunchRejected) as err:
        authorize(_a_real_program(), ["-V\x00; rm -rf /"])
    assert err.value.code == ARGS_INVALID


def test_too_many_arguments_are_refused():
    with pytest.raises(ProcessLaunchRejected) as err:
        authorize(_a_real_program(), ["-V"] * (MAX_ARGS + 1))
    assert err.value.code == ARGS_INVALID


# ---------------------------------------------------------------------------
# Identità — il PID da solo non identifica niente
# ---------------------------------------------------------------------------


def test_start_records_the_identity_of_what_it_started():
    started = procs.start_process(_a_real_program(), ["-V"])
    assert started.get("ok", True) is not False, started
    pid = started["pid"]
    assert started["identity_recorded"] is True
    assert started["exe"] == str(Path(_a_real_program()).resolve())
    recorded = ident.get_registry().get(pid)
    assert recorded is not None
    assert recorded.pid == pid
    procs.terminate_process(pid)


def test_start_refuses_and_records_nothing_when_the_policy_says_no():
    # `/bin/sh` non esiste su Windows e verrebbe rifiutato come percorso non
    # assoluto: il codice sarebbe quello sbagliato e il test proverebbe altro.
    # L'interprete corrente c'e' su entrambe le piattaforme.
    out = procs.start_process(sys.executable, ["-c", "print('pwned')"])
    assert out["ok"] is False
    assert out["code"] == INTERPRETER_INLINE_CODE_FORBIDDEN
    assert ident.get_registry().known_pids() == []


def test_a_process_we_started_can_be_terminated_without_ceremony():
    started = procs.start_process(_a_real_program(), ["-V"])
    pid = started["pid"]
    out = procs.terminate_process(pid)
    assert out.get("ok") is True, out
    assert ident.get_registry().get(pid) is None


def test_a_recycled_pid_is_not_terminated_in_place_of_the_original():
    """Il caso che il PID da solo non distingue: stesso numero, altro processo."""
    started = procs.start_process(_a_real_program(), ["-V"])
    pid = started["pid"]
    recorded = ident.get_registry().get(pid)
    assert recorded is not None
    # Stesso PID, nato in un altro momento: e' un altro processo.
    ident.get_registry().record(
        ident.ProcessIdentity(
            pid=pid,
            create_time=(recorded.create_time or time.time()) - 10_000.0,
            exe=recorded.exe,
            owner=recorded.owner,
        )
    )
    out = procs.terminate_process(pid)
    assert out["ok"] is False, out
    assert out["code"] == ident.PROCESS_IDENTITY_MISMATCH
    # Il record obsoleto non sopravvive a coprire il PID riciclato.
    assert ident.get_registry().get(pid) is None


UNRELATED_PID = 42  # processo preesistente nel backend: non l'abbiamo avviato noi


def test_a_process_we_did_not_start_needs_an_explicit_expectation():
    """Su main bastava il numero: `terminate_process(pid)` uccideva un estraneo."""
    before = procs.get_process(UNRELATED_PID)
    assert before is not None and before.get("status") == "running"

    out = procs.terminate_process(UNRELATED_PID)

    assert out["ok"] is False, out
    assert out["code"] == ident.PROCESS_IDENTITY_REQUIRED
    after = procs.get_process(UNRELATED_PID)
    assert after.get("status") == "running", "il processo estraneo e' stato toccato"


def test_an_expectation_that_does_not_match_refuses_before_the_effect():
    out = procs.terminate_process(UNRELATED_PID, expect_create_time=1.0)

    assert out["ok"] is False, out
    assert out["code"] == ident.PROCESS_IDENTITY_MISMATCH
    assert procs.get_process(UNRELATED_PID).get("status") == "running"


def test_a_wrong_name_expectation_refuses_before_the_effect():
    out = procs.terminate_process(UNRELATED_PID, expect_name="qualcos-altro.exe")

    assert out["ok"] is False, out
    assert out["code"] == ident.PROCESS_IDENTITY_MISMATCH
    assert procs.get_process(UNRELATED_PID).get("status") == "running"


def test_a_matching_expectation_allows_the_termination():
    info = procs.get_process(UNRELATED_PID)
    assert info is not None

    out = procs.terminate_process(
        UNRELATED_PID, expect_create_time=info.get("create_time")
    )

    assert out.get("ok") is True, out


# ---------------------------------------------------------------------------
# EDGE — i confini
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pid", [0, 1, -1, -100])
def test_protected_pids_are_never_terminated(pid):
    out = procs.terminate_process(pid)
    assert out["ok"] is False
    assert out["code"] == ident.PROCESS_PROTECTED


@pytest.mark.parametrize("bad", ["abc", None, [], {}])
def test_a_pid_that_is_not_a_pid_is_refused(bad):
    out = procs.terminate_process(bad)
    assert out["ok"] is False
    assert out["code"] in {ident.PROCESS_NOT_FOUND, ident.PROCESS_PROTECTED}


def test_terminating_something_that_no_longer_exists_reports_it_and_forgets():
    started = procs.start_process(_a_real_program(), ["-V"])
    pid = started["pid"]
    assert procs.terminate_process(pid).get("ok") is True
    again = procs.terminate_process(pid)
    assert again["ok"] is False
    assert again["code"] in {ident.PROCESS_NOT_FOUND, ident.PROCESS_IDENTITY_REQUIRED}


def test_identity_comparison_is_fail_closed_when_nothing_can_be_compared():
    a = ident.ProcessIdentity(pid=10)
    b = ident.ProcessIdentity(pid=10)
    assert ident.same_process(a, b) is False, "due identita' vuote non sono una prova"


def test_identity_falls_back_to_the_executable_when_create_time_is_unknown():
    a = ident.ProcessIdentity(pid=10, exe="/usr/bin/tool")
    b = ident.ProcessIdentity(pid=10, exe="/usr/bin/tool")
    assert ident.same_process(a, b) is True
    c = ident.ProcessIdentity(pid=10, exe="/usr/bin/other")
    assert ident.same_process(a, c) is False


def test_a_different_pid_is_never_the_same_process():
    a = ident.ProcessIdentity(pid=10, create_time=100.0)
    b = ident.ProcessIdentity(pid=11, create_time=100.0)
    assert ident.same_process(a, b) is False


def test_identity_from_a_malformed_backend_answer_does_not_explode():
    for junk in (None, "stringa", [], 42, {"pid": "non-numerico"}):
        got = ident.identity_from_process(junk, pid=7)
        assert got.pid == 7
        assert got.create_time is None or isinstance(got.create_time, float)
