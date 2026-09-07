"""Semantic mapper — map natural language / intents to UI elements."""
from __future__ import annotations
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

def map_intent_to_element(tree: dict[str, Any], intent: str) -> dict[str, Any] | None:
    intent_l = intent.lower().strip()
    nodes = flatten(tree)
    # Direct synonym match
    for key, syns in SYNONYMS.items():
        if intent_l == key or intent_l in syns or any(s in intent_l for s in syns):
            for n in nodes:
                name = (n.get("name") or "").lower()
                aid = (n.get("automation_id") or "").lower()
                if key.replace("_", " ") in name or key.replace("_", ".") in aid or key in aid:
                    return n
                if any(s in name for s in syns):
                    return n
    # Fuzzy: substring on name
    for n in nodes:
        name = (n.get("name") or "").lower()
        if intent_l in name or name in intent_l:
            return n
    return None

def suggest_mappings(tree: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = flatten(tree)
    suggestions = []
    for key, syns in SYNONYMS.items():
        for n in nodes:
            name = (n.get("name") or "").lower()
            if any(s in name for s in syns) or key.replace("_", " ") in name:
                suggestions.append({"semantic": key, "element": n, "confidence": 0.85})
                break
    return suggestions
