"""Helpers for normalizing and merging knowledge tree entries."""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional


def knowledge_tree_key(item: Mapping[str, Any]) -> str | None:
    namespace = item.get("namespace")
    identifier = item.get("identifier")
    if namespace and identifier:
        return f"{namespace}:{identifier}"
    coordinate = item.get("coordinate")
    if coordinate:
        return str(coordinate)
    return None


def normalize_knowledge_tree_item(item: Any) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    if isinstance(item, Mapping):
        for key in ("relevance_score", "tier_rank", "score"):
            if key in item:
                extras[key] = item[key]

    if isinstance(item, str):
        try:
            parsed = json.loads(item.replace("'", "\""))
            if isinstance(parsed, dict):
                return normalize_knowledge_tree_item(parsed)
        except Exception:
            return {"coordinate": item, **extras}

    if isinstance(item, Mapping):
        namespace = item.get("namespace")
        identifier = item.get("identifier")
        if namespace and identifier:
            return {"namespace": str(namespace), "identifier": str(identifier), **extras}
        coordinate = item.get("coordinate")
        if isinstance(coordinate, Mapping):
            namespace = coordinate.get("namespace")
            identifier = coordinate.get("identifier")
            if namespace and identifier:
                return {"namespace": str(namespace), "identifier": str(identifier), **extras}
        if coordinate is not None:
            return {"coordinate": str(coordinate), **extras}
        return {"coordinate": json.dumps(item, sort_keys=True), **extras}

    if hasattr(item, "namespace") and hasattr(item, "identifier"):
        return {
            "namespace": str(getattr(item, "namespace")),
            "identifier": str(getattr(item, "identifier")),
            **extras,
        }
    if hasattr(item, "as_path"):
        return {"coordinate": str(getattr(item, "as_path")()), **extras}
    return {"coordinate": str(item), **extras}


def merge_knowledge_trees(
    *trees: Optional[list[Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for tree in trees:
        for item in tree or []:
            normalized = normalize_knowledge_tree_item(item)
            key = knowledge_tree_key(normalized) or json.dumps(normalized, sort_keys=True)
            if key in merged:
                del merged[key]
            merged[key] = normalized

    items = list(merged.values())
    if limit is not None and len(items) > limit:
        items = items[-limit:]
    return items
