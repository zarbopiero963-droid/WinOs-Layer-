"""Il percorso semantico deve funzionare su nomi che il progetto non conosce."""
from __future__ import annotations

from windows_os_api.apps.semantic.mapper import (
    map_intent_to_element,
    suggest_mappings,
)


def _unknown_tree(label: str) -> dict:
    return {
        "name": "finestra-generata",
        "automation_id": "root",
        "control_type": "Window",
        "children": [
            {
                "name": "",
                "automation_id": "decorazione-senza-nome",
                "control_type": "Pane",
                "children": [],
            },
            {
                "name": label,
                "automation_id": "id-generato-a-runtime",
                "control_type": "Edit",
                "states": ["editable"],
                "value": "originale",
                "children": [],
            },
        ],
    }


def test_an_empty_accessible_name_cannot_match_every_unknown_query():
    label = "campo-7f31b29a-che-non-e-nel-prodotto"
    matched = map_intent_to_element(_unknown_tree(label), f"imposta {label}")

    assert matched is not None
    assert matched["automation_id"] == "id-generato-a-runtime"


def test_unknown_actionable_controls_receive_a_generic_semantic_mapping():
    label = "campo-0d42e6f1-che-non-e-nella-tabella-sinonimi"
    suggestions = suggest_mappings(_unknown_tree(label))

    assert any(
        item["element"]["automation_id"] == "id-generato-a-runtime"
        and item["semantic"] == label
        for item in suggestions
    ), suggestions


def test_actionable_control_wins_over_an_identically_named_static_label():
    label = "campo-win32-81ac7e-non-preconfigurato"
    tree = _unknown_tree(label)
    tree["children"].insert(
        1,
        {
            "name": label,
            "automation_id": "etichetta-statica-generata",
            "control_type": "Text",
            "children": [],
        },
    )

    matched = map_intent_to_element(tree, f"imposta {label}")

    assert matched is not None
    assert matched["automation_id"] == "id-generato-a-runtime"
