from __future__ import annotations

import sys
import types
from importlib import import_module
from pathlib import Path

backend_pkg = types.ModuleType("backend")
backend_pkg.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules.setdefault("backend", backend_pkg)

knowledge_tree_utils = import_module("backend.utils.knowledge_tree")


def test_knowledge_tree_trims_to_most_recent() -> None:
    tree = [
        {"coordinate": "alpha:1"},
        {"coordinate": "alpha:2"},
        {"coordinate": "alpha:1"},
        {"namespace": "alpha", "identifier": "3", "extra": {"ignored": True}},
        {"coordinate": {"namespace": "alpha", "identifier": "4", "metadata": {"x": 1}}},
        {"coordinate": "alpha:5"},
    ]

    merged = knowledge_tree_utils.merge_knowledge_trees(tree, limit=3)

    assert merged == [
        {"namespace": "alpha", "identifier": "3"},
        {"namespace": "alpha", "identifier": "4"},
        {"coordinate": "alpha:5"},
    ]
