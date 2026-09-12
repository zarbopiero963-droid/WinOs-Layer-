"""Semantic mapper — map natural language / intents to UI elements."""
from __future__ import annotations

import re
from typing import Any

from windows_os_api.apps.ui_inspector.service import flatten

# Synonym tables for CRM-like UIs
SYNONYMS: dict[str, list[str]] = {
    "save": ["save", "salva", "confirm", "submit", "applica"],
    "cancel": ["cancel", "annulla", "close", "chiudi"],
    "customer_name": ["customer name", "nome", "name", "ragione sociale"],
    "email": ["email", "e-mail", "mail", "posta"],
    "phone": ["phone", "telefono", "tel", "mobile"],
    "search": ["search", "cerca", "find", "trova"],
    "new_customer": ["new customer", "nuovo cliente", "add customer", "crea cliente"],
}


def _normalise(value: Any) -> str:
    """Return comparable words without relying on app-specific punctuation."""
    return " ".join(re.findall(r"\w+", str(value or "").casefold()))


def _is_actionable(node: dict[str, Any]) -> bool:
    control_type = _normalise(node.get("control_type"))
    states = {_normalise(state) for state in (node.get("states") or [])}
    return control_type in {
        "button",
        "checkbox",
        "combobox",
        "document",
        "edit",
        "entry",
        "hyperlink",
        "listitem",
        "menuitem",
        "radiobutton",
        "slider",
        "tabitem",
    } or "editable" in states

def map_intent_to_element(tree: dict[str, Any], intent: str) -> dict[str, Any] | None:
    intent_l = _normalise(intent)
    if not intent_l:
        return None
    nodes = flatten(tree)
    # Direct synonym match
    for key, syns in SYNONYMS.items():
        key_words = _normalise(key)
        normalised_syns = [_normalise(s) for s in syns]
        if (
            intent_l == key_words
            or intent_l in normalised_syns
            or any(s in intent_l for s in normalised_syns)
        ):
            for n in nodes:
                name = _normalise(n.get("name"))
                aid = _normalise(n.get("automation_id"))
                if key_words in name or key_words in aid:
                    return n
                if any(s in name for s in normalised_syns):
                    return n
    # Generic fallback for controls whose labels are unknown at development
    # time. Empty names must never match ("" is a substring of every query).
    best: tuple[int, int, int, dict[str, Any]] | None = None
    for n in nodes:
        for candidate in (_normalise(n.get("name")), _normalise(n.get("automation_id"))):
            if not candidate:
                continue
            score = 3 if candidate == intent_l else 0
            if not score and candidate in intent_l:
                score = 2
            if not score and intent_l in candidate:
                score = 1
            # Native Win32 and web accessibility trees commonly expose both a
            # static label and its editable control with the same accessible
            # name. At equal textual quality the control that can fulfil the
            # intent must win; otherwise reasoning returns a decorative Text
            # node and no executable workflow can be generated.
            ranked = (score, int(_is_actionable(n)), len(candidate), n)
            if score and (best is None or ranked[:3] > best[:3]):
                best = ranked
    return best[3] if best is not None else None

def suggest_mappings(tree: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = flatten(tree)
    suggestions = []
    for key, syns in SYNONYMS.items():
        for n in nodes:
            name = (n.get("name") or "").lower()
            if any(s in name for s in syns) or key.replace("_", " ") in name:
                suggestions.append({"semantic": key, "element": n, "confidence": 0.85})
                break
    known = {
        (
            item["element"].get("automation_id"),
            item["element"].get("name"),
            item["element"].get("control_type"),
        )
        for item in suggestions
    }
    for node in nodes:
        identity = (
            node.get("automation_id"),
            node.get("name"),
            node.get("control_type"),
        )
        name = str(node.get("name") or "").casefold().strip()
        if name and identity not in known and _is_actionable(node):
            suggestions.append(
                {"semantic": name, "element": node, "confidence": 0.65}
            )
    return suggestions
