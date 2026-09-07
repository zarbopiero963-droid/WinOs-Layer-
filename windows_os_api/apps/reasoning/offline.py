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

def _ensure_llm() -> LLMProvider:
    """Lazily attach remote AI client when configured."""
    global _llm
    if not isinstance(_llm, NullLLM):
        return _llm
    try:
        from windows_os_api.apps.ai.settings_store import get_ai_settings
        from windows_os_api.apps.ai.provider import get_ai_client
        if get_ai_settings().remote_ready():
            _llm = get_ai_client()
    except Exception:  # noqa: BLE001
        pass
    return _llm

def reason(query: str, hwnd: int = 1001) -> dict[str, Any]:
    tree = get_tree(hwnd)
    element = map_intent_to_element(tree, query)
    suggestions = suggest_mappings(tree)
    llm = _ensure_llm()
    llm_hint = llm.complete(f"UI query: {query}")
    engine = "deterministic-offline"
    try:
        from windows_os_api.apps.ai.settings_store import get_ai_settings
        s = get_ai_settings()
        if s.remote_ready() and llm_hint:
            engine = f"hybrid:{s.provider}"
        elif s.remote_ready():
            engine = f"deterministic+{s.provider}-ready"
    except Exception:  # noqa: BLE001
        pass
    return {
        "query": query,
        "matched_element": element,
        "suggestions": suggestions[:10],
        "node_count": len(flatten(tree)),
        "llm_hint": llm_hint or None,
        "engine": engine,
    }
