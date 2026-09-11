"""Universal Adapter engine — turns EXE UI into virtual API actions."""
from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable
from windows_os_api.apps.ui_inspector.service import find_by_automation_id, get_tree
from windows_os_api.apps.sandbox.permissions import check_action
from windows_os_api.apps.adapters import store
from windows_os_api.apps.adapters.validation import validate_app_id
from windows_os_api.backends.factory import get_backend

@dataclass
class AdapterAction:
    name: str
    description: str
    automation_id: str
    control_type: str
    params: list[str] = field(default_factory=list)
    risk: str = "low"
    # `None` = mai provata. Non e' lo stesso di «non funziona»: e' «nessuno ha
    # guardato». Il verdetto, quando c'e', porta con se' l'evidenza che lo
    # sostiene (vedi `verification.py`).
    verification: dict[str, Any] | None = None

@dataclass
class Adapter:
    app_id: str
    app_name: str
    # `None` = ricaricato da disco e non ancora riagganciato a una finestra viva.
    # Un `hwnd` e' un numero che il sistema operativo riassegna: quello salvato
    # ieri puo' appartenere oggi a un'altra applicazione, quindi un adapter
    # ricaricato sa cosa fare ma non piu' su cosa. Il ragionamento per esteso e'
    # in `store.py`.
    hwnd: int | None
    actions: list[AdapterAction] = field(default_factory=list)
    trust_level: str = "unsigned"
    openapi: dict[str, Any] = field(default_factory=dict)
    # Il manifest e' stato scritto? Un disco pieno o una cartella non
    # scrivibile non devono far fallire un adapter che in memoria funziona —
    # ma non devono nemmeno passare inosservati, o al riavvio l'adapter
    # sparisce e nessuno sa perche'.
    persisted: bool = False
    persist_error: str | None = None
    # Verifica e rollback formano una transazione sull'applicazione. Due probe
    # concorrenti sullo stesso adapter potrebbero scambiarsi i valori originali
    # e ripristinare la sonda dell'altro; il lock resta solo in memoria.
    _verification_lock: RLock = field(default_factory=RLock, repr=False, compare=False)

    @property
    def bound(self) -> bool:
        return self.hwnd is not None

_adapters: dict[str, Adapter] = {}

ADAPTER_NOT_BOUND = "ADAPTER_NOT_BOUND"

def _default_actions_from_tree(tree: dict[str, Any]) -> list[AdapterAction]:
    actions: list[AdapterAction] = []

    def action_id(automation_id: str) -> str:
        legacy = automation_id.replace(".", "_")
        if re.fullmatch(r"[a-zA-Z0-9_-]+", legacy):
            return legacy
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", automation_id).strip("_")
        digest = hashlib.sha256(automation_id.encode("utf-8")).hexdigest()[:10]
        return f"{(safe or 'control')[:80]}_{digest}"

    def walk(node: dict[str, Any]) -> None:
        ct = node.get("control_type")
        ct_key = str(ct or "").casefold()
        states = {str(state).casefold() for state in (node.get("states") or [])}
        aid = node.get("automation_id") or ""
        name = node.get("name") or aid or "unknown"
        if ct == "Button" and aid:
            actions.append(AdapterAction(
                name=f"click_{action_id(aid)}",
                description=f"Click button {name}",
                automation_id=aid,
                control_type=ct,
                risk="medium" if "delete" in name.lower() or "exit" in name.lower() else "low",
            ))
        editable = ct_key in {"edit", "document", "entry", "password text"}
        editable = editable or (ct_key == "text" and "editable" in states)
        if editable and aid:
            actions.append(AdapterAction(
                name=f"set_{action_id(aid)}",
                description=f"Set field {name}",
                automation_id=aid,
                # Canonical type: Windows UIA calls it Edit/Document, AT-SPI
                # commonly calls the same editable control text/entry.
                control_type="Edit",
                params=["value"],
                risk="low",
            ))
        if ct == "MenuItem" and aid and not node.get("children"):
            actions.append(AdapterAction(
                name=f"menu_{action_id(aid)}",
                description=f"Invoke menu {name}",
                automation_id=aid,
                control_type=ct,
                risk="medium",
            ))
        for c in node.get("children") or []:
            walk(c)
    walk(tree)
    return actions

def create_adapter(app_id: str, hwnd: int = 1001, trust_level: str = "unsigned") -> Adapter:
    # Validated here rather than in each caller: this is the point that binds a
    # name to a running application, and an adapter registered under "" is one
    # nothing can look up again.
    app_id = validate_app_id(app_id)
    tree = get_tree(hwnd)
    actions = _default_actions_from_tree(tree)
    adapter = Adapter(
        app_id=app_id,
        app_name=tree.get("name") or app_id,
        hwnd=hwnd,
        actions=actions,
        trust_level=trust_level,
    )
    adapter.openapi = generate_adapter_openapi(adapter)
    _adapters[app_id] = adapter
    try:
        store.save(adapter)
        adapter.persisted = True
    except OSError as exc:
        # Non si solleva: l'adapter in memoria funziona, e far fallire la
        # creazione per un problema di disco sarebbe peggio. Ma il fatto resta
        # scritto sull'adapter e viene riportato da `list_adapters`.
        adapter.persist_error = str(exc)
    return adapter

def rebind_adapter(app_id: str, hwnd: int) -> Adapter | None:
    """Riaggancia un adapter ricaricato a una finestra viva.

    E' il passo che manca dopo un riavvio: la descrizione e' tornata dal disco,
    ma su quale finestra applicarla lo sa solo chi chiama, adesso.
    """
    adapter = _adapters.get(app_id)
    if adapter is None:
        return None
    adapter.hwnd = hwnd
    return adapter

def load_persisted_adapters() -> dict[str, Any]:
    """Rimette in memoria gli adapter salvati, **non agganciati**.

    Restituisce anche i manifest scartati: un adapter che sparisce in silenzio
    e' un adapter che il chiamante crede di avere.

    Gli adapter gia' in memoria non vengono toccati — uno vivo e agganciato vale
    piu' della sua fotografia su disco.
    """
    manifests, skipped = store.load_all()
    restored: list[str] = []
    for manifest in manifests:
        app_id = manifest["app_id"]
        if app_id in _adapters:
            continue
        adapter = Adapter(
            app_id=app_id,
            app_name=manifest.get("app_name") or app_id,
            hwnd=None,  # noto, non utilizzabile: va riagganciato
            actions=[
                AdapterAction(
                    name=a.get("name", ""),
                    description=a.get("description", ""),
                    automation_id=a.get("automation_id", ""),
                    control_type=a.get("control_type", ""),
                    params=list(a.get("params") or []),
                    risk=a.get("risk", "low"),
                    verification=a.get("verification"),
                )
                for a in manifest["actions"]
                if isinstance(a, dict)
            ],
            trust_level=manifest.get("trust_level", "unsigned"),
        )
        # Rigenerato, mai riletto dal disco: un documento derivato salvato
        # accanto alla sua sorgente e' un modo per farli divergere.
        adapter.openapi = generate_adapter_openapi(adapter)
        _adapters[app_id] = adapter
        restored.append(app_id)
    return {
        "restored": restored,
        "skipped": [
            {"path": s.path, "code": s.code, "reason": s.reason} for s in skipped
        ],
    }

def verify_and_record(app_id: str, action_name: str, times: int = 1) -> dict[str, Any]:
    """Prova un'azione, registra il verdetto sull'azione e lo rende persistente.

    Il verdetto vive con l'adapter — non in un rapporto che si perde alla
    chiusura del runtime: al prossimo avvio si deve poter sapere cosa era gia'
    stato dimostrato, senza rifare tutte le prove.
    """
    from windows_os_api.apps.adapters.verification import (
        MAX_VERIFICATION_ATTEMPTS,
        verify_action,
        verify_action_repeatedly,
    )

    if times < 1 or times > MAX_VERIFICATION_ATTEMPTS:
        return {
            "ok": False,
            "recorded": False,
            "error": f"times must be between 1 and {MAX_VERIFICATION_ATTEMPTS}",
        }

    adapter = _adapters.get(app_id)
    if adapter is None:
        return {"ok": False, "error": f"nessun adapter registrato per {app_id!r}"}
    action = next((a for a in adapter.actions if a.name == action_name), None)
    if action is None:
        return {"ok": False, "error": f"azione non trovata: {action_name}"}

    with adapter._verification_lock:
        verdict = (
            verify_action(app_id, action_name)
            if times <= 1
            else verify_action_repeatedly(app_id, action_name, times=times)
        )
        action.verification = verdict
        persisted = True
        try:
            store.save(adapter)
            adapter.persisted = True
            adapter.persist_error = None
        except OSError as exc:
            persisted = False
            adapter.persist_error = str(exc)
        verified = verdict.get("state") == "VERIFIED"
        return {
            "ok": bool(verified and persisted),
            "recorded": True,
            "persisted": persisted,
            "app_id": app_id,
            "action": action_name,
            "verification": verdict,
        }


def get_adapter(app_id: str) -> Adapter | None:
    return _adapters.get(app_id)

def list_adapters() -> list[dict[str, Any]]:
    return [
        {"app_id": a.app_id, "app_name": a.app_name, "hwnd": a.hwnd,
         "bound": a.bound, "persisted": a.persisted,
         "persist_error": a.persist_error,
         "actions": len(a.actions), "trust_level": a.trust_level}
        for a in _adapters.values()
    ]

def _ui_tree_empty(tree: dict[str, Any]) -> bool:
    if not tree:
        return True
    if tree.get("supported") is False:
        return True
    kids = tree.get("children") or []
    if not kids and tree.get("control_type") in (None, "", "Unsupported"):
        return True
    return False


def invoke_action(app_id: str, action_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    adapter = _adapters.get(app_id)
    if not adapter:
        return {"ok": False, "error": "adapter not found"}
    action = next((a for a in adapter.actions if a.name == action_name), None)
    if not action:
        return {"ok": False, "error": f"action not found: {action_name}"}

    # Un adapter ricaricato da disco sa COSA fare, non piu' su cosa: l'`hwnd`
    # salvato e' un numero che il sistema riassegna, e agire su di esso
    # significherebbe cliccare su una finestra che non e' quella che si crede —
    # potenzialmente di un'altra applicazione. Si rifiuta, e si dice come
    # rimediare, invece di indovinare la finestra.
    if not adapter.bound:
        return {
            "ok": False,
            "error": (
                f"l'adapter {app_id!r} e' stato ricaricato da disco e non e' "
                f"agganciato a nessuna finestra: riagganciarlo con un hwnd vivo "
                f"prima di invocare azioni"
            ),
            "code": ADAPTER_NOT_BOUND,
            "app_id": app_id,
            "action": action_name,
            "bound": False,
        }

    params = params or {}
    # Sandbox enforcement lives HERE, not in the callers.
    #
    # It used to read "delegated to caller", and of the four call sites only one
    # honoured it: api/rest/apps.py. The other three reached the backend with the
    # policy unchecked — api/rest/workflows.py via recorder.play, api/mcp/server.py
    # (the tool an AI agent drives) and apps/agent/computer.py. A policy set with
    # PUT /v1/sandbox/policy was therefore enforced on one surface out of four.
    #
    # Centralising it here makes the gate unbypassable by construction: any future
    # caller is covered without having to remember. The REST route keeps its own
    # check so it can answer 403 — check_action is pure, so checking twice is free.
    gate = check_action(app_id, action_name, action.risk)
    if not gate["allowed"]:
        return {
            "ok": False,
            "denied": True,
            "error": gate["reason"],
            "app_id": app_id,
            "action": action_name,
        }
    backend = get_backend()
    tree = backend.get_ui_tree(adapter.hwnd)
    node = find_by_automation_id(tree, action.automation_id)
    if not node:
        vision_result = None
        if _ui_tree_empty(tree) or getattr(backend, "name", "") == "linux":
            needle = (
                params.get("text")
                or (action.description or "").replace("Click button ", "")
                or action.name.replace("click_", "").replace("_", " ")
            )
            try:
                from windows_os_api.apps.vision.ocr import click_text

                vision_result = click_text(
                    str(needle),
                    dry_run=bool(params.get("dry_run", True)),
                )
                if vision_result.get("ok"):
                    return {
                        "ok": True,
                        "action": action_name,
                        "fallback": "vision",
                        "vision": vision_result,
                        "automation_id": action.automation_id,
                    }
            except Exception as e:  # noqa: BLE001
                vision_result = {"ok": False, "error": str(e)}
        return {
            "ok": False,
            "error": "UI element not found",
            "automation_id": action.automation_id,
            "vision": vision_result,
            "fallback_attempted": "vision" if vision_result is not None else None,
        }
    if action.control_type == "Edit":
        value = params.get("value", "")
        # NOTA — qui c'era `node["value"] = value`, e non faceva quello che
        # sembrava: `node` appartiene all'albero RESTITUITO da `get_ui_tree`,
        # cioe' a una copia. Scriverci dentro non cambiava l'applicazione;
        # sembrava funzionare solo perche' la copia del FakeBackend era
        # superficiale e condivisa. L'effetto ora passa dal backend, che e'
        # l'unico che possa produrlo davvero — e la verifica delle capability
        # lo rilegge da li'.
        setter = getattr(backend, "set_ui_value", None)
        if callable(setter):
            set_result = setter(action.automation_id, str(value))
            if set_result.get("ok"):
                return {
                    "ok": True,
                    "action": action_name,
                    "set_value": value,
                    "element": action.automation_id,
                }
        # Prefer real UIA ValuePattern / SendInput set_value on Windows
        if hasattr(backend, "name") and backend.name == "windows":
            try:
                from windows_os_api.apps.ui_inspector import uia_windows
                set_r = uia_windows.set_value(node, str(value))
                return {
                    "ok": bool(set_r.get("ok")),
                    "action": action_name,
                    "set_value": value,
                    "element": action.automation_id,
                    "uia": set_r,
                }
            except Exception as e:  # noqa: BLE001
                typed = backend.type_text(str(value))
                return {
                    "ok": bool(typed.get("ok")),
                    "action": action_name,
                    "set_value": value,
                    "element": action.automation_id,
                    "fallback": str(e),
                }
        backend.type_text(str(value))
        return {"ok": True, "action": action_name, "set_value": value, "element": action.automation_id}
    if action.control_type in ("Button", "MenuItem"):
        if hasattr(backend, "name") and backend.name == "windows":
            try:
                from windows_os_api.apps.ui_inspector import uia_windows
                click_r = uia_windows.invoke_click(node)
                return {
                    "ok": bool(click_r.get("ok")),
                    "action": action_name,
                    "clicked": action.automation_id,
                    "uia": click_r,
                }
            except Exception:  # noqa: BLE001
                pass
        bounds = node.get("bounds") or {}
        if bounds.get("width"):
            x = int(bounds["left"] + bounds["width"] // 2)
            y = int(bounds["top"] + bounds.get("height", 1) // 2)
            backend.mouse_click(x, y)
        else:
            backend.mouse_click(10, 10)
        return {"ok": True, "action": action_name, "clicked": action.automation_id}
    return {"ok": True, "action": action_name, "element": action.automation_id}

def generate_adapter_openapi(adapter: Adapter) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for a in adapter.actions:
        path = f"/v1/apps/{adapter.app_id}/actions/{a.name}"
        props = {p: {"type": "string"} for p in a.params}
        paths[path] = {
            "post": {
                "summary": a.description,
                "operationId": a.name,
                "requestBody": {
                    "content": {"application/json": {"schema": {"type": "object", "properties": props}}}
                } if a.params else None,
                "responses": {"200": {"description": "OK"}},
                "x-risk": a.risk,
            }
        }
    return {
        "openapi": "3.0.3",
        "info": {"title": f"{adapter.app_name} Virtual API", "version": "1.0.0"},
        "paths": paths,
    }

def reset_adapters() -> None:
    _adapters.clear()
