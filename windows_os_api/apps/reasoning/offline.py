"""Deterministic offline UI reasoner + pluggable LLM interface."""
from __future__ import annotations
from typing import Any, Protocol
from windows_os_api.apps.semantic.mapper import map_intent_to_element, suggest_mappings
from windows_os_api.apps.ui_inspector.service import get_tree, flatten

class LLMProvider(Protocol):
    def complete(self, prompt: str) -> str: ...

class NullLLM:
    def complete(self, prompt: str) -> str:
        return ""

_llm: LLMProvider = NullLLM()

def set_llm(provider: LLMProvider) -> None:
    global _llm
    _llm = provider

def reason(query: str, hwnd: int = 1001) -> dict[str, Any]:
    tree = get_tree(hwnd)
    element = map_intent_to_element(tree, query)
    suggestions = suggest_mappings(tree)
    llm_hint = _llm.complete(f"UI query: {query}")
    return {
        "query": query,
        "matched_element": element,
        "suggestions": suggestions[:10],
        "node_count": len(flatten(tree)),
        "llm_hint": llm_hint or None,
        "engine": "deterministic-offline",
    }
