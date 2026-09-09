r"""La policy sandbox vale anche per le primitive UI grezze.

Il buco che chiude
------------------
`check_action` sta dentro `invoke_action`, quindi ogni superficie che passa
dall'engine — REST `/v1/apps/...`, MCP, workflow, agente — e' coperta. Ma tre
rotte agiscono sull'interfaccia **senza passare da li'**::

    api/rest/ui.py:110  ->  ui.accessible_click(name, role)
    api/rest/ui.py:118  ->  ui.accessible_set_text(name, text, role)
    api/rest/ui.py:222  ->  ui.click_text_vision(text, dry_run)

Misurato su `main` prima di questa patch, con
`SandboxPolicy(app_id="contoso-crm", denied_actions={"click_btn_save"})` in
vigore::

    via invoke_action      ->  ok=False  denied=True  error='explicitly denied'
    via click_text_vision  ->  denied=None            error='text not found'

La seconda riga e' il difetto: nessuna policy e' stata consultata. Si e' fermata
solo perche' su uno schermo vuoto non c'era testo da trovare — non perche'
qualcosa l'abbia bloccata. Con un'applicazione vera davanti, quel click parte.

Quindi negare `click_btn_save` non impediva di premere lo stesso pulsante:
bastava chiamarlo per nome, o per il testo che ci si legge sopra. Una seconda
porta sulla stessa stanza, e la policy per-azione sorvegliava solo la prima.

Cosa NON e' cambiato
--------------------
Le primitive restano protette dal RBAC (permission `ui.control`): non erano
aperte a chiunque, ed e' il motivo per cui il difetto non e' una porta
spalancata. Qui si aggiunge il secondo controllo, quello per-azione.

Come si collega un nome a un'azione
------------------------------------
La policy parla di `app_id` + nome dell'azione; una primitiva grezza conosce
solo un'etichetta o del testo a schermo. Il collegamento passa dagli adapter
registrati: se una loro azione punta a quel controllo — per `automation_id`, per
nome o per descrizione — e la policy la nega, la primitiva viene rifiutata.

Il confronto ignora maiuscole e punteggiatura (`btn.save`, `click_btn_save` e
`Click button Save` sono lo stesso pulsante). Un bersaglio che nessun adapter
riconosce passa: non era coperto da nessuna policy nemmeno prima, e inventare un
divieto qui vorrebbe dire vietare mezza interfaccia.

Direzione dell'errore: un omonimo in un'altra applicazione viene rifiutato
insieme al bersaglio vero. E' il verso giusto in cui sbagliare — il rifiuto dice
quale policy ha deciso, quindi si vede e si corregge; un click che parte non si
vede.
"""
from __future__ import annotations

from typing import Any

UI_TARGET_DENIED = "UI_TARGET_DENIED"

# Sotto i tre caratteri un bersaglio non identifica niente: confrontarlo
# significherebbe far combaciare qualunque cosa.
_MIN_TARGET = 3


def _flat(value: object) -> str:
    """Solo lettere e cifre, maiuscole: `btn.save`, `click_btn_save` e
    `Click button Save` finiscono per essere confrontabili fra loro."""
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value.upper() if ch.isalnum())


def _identifiers(action: Any) -> set[str]:
    """Le forme con cui un'azione puo' essere chiamata da fuori dall'adapter."""
    out: set[str] = set()
    automation_id = getattr(action, "automation_id", "") or ""
    if automation_id:
        out.add(_flat(automation_id))
        out.add(_flat(automation_id.rsplit(".", 1)[-1]))
    name = getattr(action, "name", "") or ""
    if name:
        out.add(_flat(name))
        out.add(_flat(name.split("_", 1)[-1]))
    description = getattr(action, "description", "") or ""
    if description:
        out.add(_flat(description))
        out.add(_flat(description.split()[-1]) if description.split() else "")
    return {value for value in out if value}


def _matches(target: str, action: Any) -> bool:
    flat = _flat(target)
    if len(flat) < _MIN_TARGET:
        return False
    for identifier in _identifiers(action):
        if identifier == flat or identifier.endswith(flat):
            return True
    return False


def check_ui_target(target: object, *, kind: str = "click") -> dict[str, Any]:
    """Autorizza una primitiva UI diretta a `target`.

    `{"allowed": True}`, oppure il motivo e l'azione di adapter che lo nega.
    Pura come `check_action`: non tocca niente, quindi controllarla due volte
    lungo la stessa catena non costa nulla.
    """
    # Import qui dentro: `engine` importa `sandbox.permissions`, e importare
    # l'engine in cima a questo modulo chiuderebbe il cerchio.
    from windows_os_api.apps.adapters.engine import _adapters
    from windows_os_api.apps.sandbox.permissions import check_action

    if not isinstance(target, str) or not target.strip():
        return {"allowed": True}

    for app_id, adapter in list(_adapters.items()):
        for action in getattr(adapter, "actions", []) or []:
            if not _matches(target, action):
                continue
            verdict = check_action(app_id, action.name, getattr(action, "risk", "low"))
            if not verdict.get("allowed"):
                return {
                    "allowed": False,
                    "reason": verdict.get("reason", "denied by sandbox policy"),
                    "app_id": app_id,
                    "action": action.name,
                    "kind": kind,
                }
    return {"allowed": True}


def rejection(verdict: dict[str, Any], target: str) -> dict[str, Any]:
    """La forma del rifiuto, uguale a quella del resto del progetto."""
    return {
        "ok": False,
        "denied": True,
        "code": UI_TARGET_DENIED,
        "error": (
            f"{verdict['kind']} su {target!r} rifiutato: la policy sandbox nega "
            f"{verdict['action']!r} per {verdict['app_id']!r} ({verdict['reason']})"
        ),
        "target": target,
        "app_id": verdict["app_id"],
        "action": verdict["action"],
    }
