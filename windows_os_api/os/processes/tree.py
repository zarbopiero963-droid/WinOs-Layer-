"""N048 — Albero di processo, inventario e teardown.

N047 ha chiuso «chi è questo PID?». N048 chiude «cosa gli sta attorno?» e
«quando lo fermo, resta qualcosa?».

Contratto (#67 / H63-N048): inventario parent/children/modules/threads/
handles/resources; restart e terminate limitati ai processi posseduti; zero
figli residui dopo il teardown.

L'inventario è **letto** dal backend (psutil o equivalente). Il teardown è
orchestrato dal service: i discendenti di un root posseduto si terminano
perché stanno nell'albero di quel root, non perché ciascuno sia nel registro.
Un PID estraneo resta negato da N047 — questa modulo non apre scorciatoie.
"""
from __future__ import annotations

from typing import Any, Callable

PROCESS_TREE_NOT_FOUND = "PROCESS_TREE_NOT_FOUND"
PROCESS_RESIDUAL_CHILDREN = "PROCESS_RESIDUAL_CHILDREN"
PROCESS_RESTART_UNKNOWN = "PROCESS_RESTART_UNKNOWN"
PROCESS_RESTART_NOT_OWNED = "PROCESS_RESTART_NOT_OWNED"

# Quanti livelli di figli attraversare al massimo. Un albero più profondo di
# così è sospetto (o un fork-bomb): meglio fallire in modo visibile che girare
# all'infinito.
_MAX_TREE_DEPTH = 32
_MAX_TREE_NODES = 256


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def child_pids(backend: Any, pid: int) -> list[int]:
    """Figli diretti di `pid`, come li vede il backend."""
    inspect = getattr(backend, "inspect_process", None)
    if callable(inspect):
        info = inspect(pid)
        if not isinstance(info, dict):
            return []
        out: list[int] = []
        for child in info.get("children") or []:
            if isinstance(child, dict):
                cpid = _safe_int(child.get("pid"))
            else:
                cpid = _safe_int(child)
            if cpid is not None and cpid > 1:
                out.append(cpid)
        return out

    # Fallback: ricostruisci dai ppid di list_processes (backend finto / minimale).
    listing = backend.list_processes() if hasattr(backend, "list_processes") else []
    out = []
    for proc in listing or []:
        if not isinstance(proc, dict):
            continue
        ppid = _safe_int(proc.get("ppid"))
        cpid = _safe_int(proc.get("pid"))
        if ppid == pid and cpid is not None and cpid > 1:
            out.append(cpid)
    return out


def descendant_pids(backend: Any, pid: int) -> list[int]:
    """Tutti i discendenti di `pid` (profondità-prima, escluso il root).

    L'ordine è profondità-prima così un teardown bottom-up può semplicemente
    invertire la lista: prima i nipoti, poi i figli, poi (a parte) il root.
    """
    ordered: list[int] = []
    stack: list[tuple[int, int]] = [(pid, 0)]
    seen: set[int] = {pid}
    while stack:
        current, depth = stack.pop()
        if depth >= _MAX_TREE_DEPTH:
            continue
        for child in child_pids(backend, current):
            if child in seen:
                continue
            seen.add(child)
            ordered.append(child)
            stack.append((child, depth + 1))
            if len(ordered) >= _MAX_TREE_NODES:
                return ordered
    return ordered


def build_tree(backend: Any, pid: int, *, depth: int = 0) -> dict[str, Any] | None:
    """Albero nested a partire da `pid`. ``None`` se il processo non c'è."""
    if depth > _MAX_TREE_DEPTH:
        return {
            "pid": pid,
            "truncated": True,
            "reason": "max_tree_depth",
            "children": [],
        }
    inspect = getattr(backend, "inspect_process", None)
    if callable(inspect):
        info = inspect(pid)
    else:
        info = backend.get_process(pid) if hasattr(backend, "get_process") else None
    if not isinstance(info, dict):
        return None

    node = {
        "pid": pid,
        "ppid": info.get("ppid"),
        "name": info.get("name"),
        "exe": info.get("exe"),
        "status": info.get("status"),
        "owner": info.get("owner"),
        "create_time": info.get("create_time"),
        "num_threads": info.get("num_threads"),
        "children": [],
    }
    for child_pid in child_pids(backend, pid):
        child_node = build_tree(backend, child_pid, depth=depth + 1)
        if child_node is not None:
            node["children"].append(child_node)
        else:
            node["children"].append({"pid": child_pid, "missing": True, "children": []})
    return node


def inventory(backend: Any, pid: int) -> dict[str, Any] | None:
    """Inventario piatto: parent, children, threads, modules, handles, resources."""
    inspect = getattr(backend, "inspect_process", None)
    if callable(inspect):
        info = inspect(pid)
        if isinstance(info, dict):
            return info
    basic = backend.get_process(pid) if hasattr(backend, "get_process") else None
    if not isinstance(basic, dict):
        return None
    children = []
    for cpid in child_pids(backend, pid):
        child = backend.get_process(cpid)
        if isinstance(child, dict):
            children.append(
                {
                    "pid": cpid,
                    "name": child.get("name"),
                    "exe": child.get("exe"),
                    "status": child.get("status"),
                }
            )
        else:
            children.append({"pid": cpid})
    return {
        **basic,
        "ppid": basic.get("ppid"),
        "children": children,
        "num_threads": basic.get("num_threads"),
        "threads": {"available": basic.get("num_threads") is not None, "count": basic.get("num_threads")},
        "modules": {"available": False, "items": []},
        "handles": {"available": False},
        "resources": {
            "cpu_percent": basic.get("cpu_percent"),
            "memory_mb": basic.get("memory_mb"),
        },
    }


def residual_alive(
    backend: Any,
    pids: list[int],
    *,
    is_alive: Callable[[Any, int], bool] | None = None,
) -> list[int]:
    """Quali PID della lista sono ancora vivi secondo il backend."""

    def _default_alive(b: Any, pid: int) -> bool:
        info = b.get_process(pid) if hasattr(b, "get_process") else None
        if not isinstance(info, dict):
            return False
        status = str(info.get("status") or "").lower()
        if status in {"terminated", "stopped", "dead", "zombie"}:
            return False
        if info.get("running") is False:
            return False
        return True

    check = is_alive or _default_alive
    return [pid for pid in pids if check(backend, pid)]


__all__ = [
    "PROCESS_TREE_NOT_FOUND",
    "PROCESS_RESIDUAL_CHILDREN",
    "PROCESS_RESTART_UNKNOWN",
    "PROCESS_RESTART_NOT_OWNED",
    "build_tree",
    "child_pids",
    "descendant_pids",
    "inventory",
    "residual_alive",
]
