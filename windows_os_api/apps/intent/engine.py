"""Intent engine — parse user intents into structured plans."""
from __future__ import annotations
from typing import Any
from windows_os_api.apps.planner.service import plan

INTENT_PATTERNS = [
    ("create_customer", ["new customer", "nuovo cliente", "create customer", "add customer"]),
    ("save", ["save", "salva"]),
    ("search", ["search", "cerca", "find"]),
    ("export", ["export", "esporta"]),
]

def parse_intent(text: str) -> dict[str, Any]:
    t = text.lower().strip()
    for name, patterns in INTENT_PATTERNS:
        if any(p in t for p in patterns):
            return {"intent": name, "raw": text, "confidence": 0.88}
    return {"intent": "unknown", "raw": text, "confidence": 0.3}

def execute_intent(text: str, app_id: str) -> dict[str, Any]:
    parsed = parse_intent(text)
    planned = plan(text, app_id=app_id)
    return {"parsed": parsed, "plan": planned}
