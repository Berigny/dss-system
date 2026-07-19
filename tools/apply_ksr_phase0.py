#!/usr/bin/env python3
"""Apply mechanical KSR-EVAL Phase 0 fixes to semantic_registry.yaml.

This script implements the mechanical fixes identified in KSR-EVAL v0.1 and
applies owner-decision resolutions for the three DECISION items. It rewrites
the registry with PyYAML (the file contains no comments, so round-trip
formatting is acceptable).
"""
from __future__ import annotations

import copy
import os
import pathlib
from typing import Any

import yaml


REGISTRY_PATH = pathlib.Path("apps/backend/backend/kernel/semantic_registry.yaml")


def _walk_add_steward(node: Any) -> None:
    """Recursively add steward_only:true to any dict with confidence P/H."""
    if isinstance(node, dict):
        conf = node.get("confidence")
        if conf in ("P", "H"):
            node["steward_only"] = True
        for value in node.values():
            _walk_add_steward(value)
    elif isinstance(node, list):
        for item in node:
            _walk_add_steward(item)


def _regenerate_glossary(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate glossary from synonym_registry, preserving existing metadata."""
    old_glossary = data.get("glossary", [])
    # Map (replacement_key, term_lower) to the original entry so that forms
    # with distinct priorities (e.g. "Terminal optimizer" vs "optimizer") stay aligned.
    form_templates: dict[tuple[str, str], dict[str, Any]] = {}
    fallback_templates: dict[str, dict[str, Any]] = {}
    for entry in old_glossary:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("replacement") or "").strip()
        term = str(entry.get("term") or "").strip().lower()
        if key and term:
            form_templates[(key, term)] = entry
        if key:
            fallback_templates.setdefault(key, entry)

    synonym_registry: dict[str, Any] = data.get("synonym_registry", {})
    new_glossary: list[dict[str, Any]] = []

    # Drift decision: align glossary priority with stripping_priority (medium).
    # This resolves KSR-1.2.0-006.
    drift_template = fallback_templates.get("divergence", {})
    if drift_template.get("priority") == "low":
        drift_template = dict(drift_template)
        drift_template["priority"] = "medium"
        fallback_templates["divergence"] = drift_template
        for term_key in list(form_templates):
            if term_key[0] == "divergence":
                updated = dict(form_templates[term_key])
                updated["priority"] = "medium"
                form_templates[term_key] = updated

    for key, forms in synonym_registry.items():
        if not isinstance(forms, list):
            continue
        for form in forms:
            template = form_templates.get((key, form.lower())) or fallback_templates.get(key, {})
            # Preserve original key order from the template, then append extras.
            entry: dict[str, Any] = {}
            for k, v in template.items():
                if k == "term":
                    entry["term"] = form
                elif k == "replacement":
                    entry["replacement"] = key
                else:
                    entry[k] = v
            if "term" not in entry:
                entry["term"] = form
            if "replacement" not in entry:
                entry["replacement"] = key
            for k in ("category", "priority", "confidence", "relation_type"):
                if k not in entry:
                    defaults = {
                        "category": "engineering",
                        "priority": "medium",
                        "confidence": "E",
                        "relation_type": "ANALOGY",
                    }
                    entry[k] = defaults[k]
            if entry["confidence"] in ("P", "H"):
                entry["steward_only"] = True
            new_glossary.append(entry)

    return new_glossary


def _fix_lattice(data: dict[str, Any]) -> None:
    """Add missing face nodes and repair reset node (KSR-1.2.0-003)."""
    lattice = data.setdefault("lattice_registry", {})
    corner = lattice.get("corner_map", {})
    edges = lattice.setdefault("bridge_edges", [])
    declared_coords = set(corner.keys()) | {e.get("coordinate") for e in edges}
    declared_coords.add(lattice.get("centroid_coordinate", "111"))

    all_coords = {f"{i}{j}{k}" for i in "012" for j in "012" for k in "012"}
    missing = sorted(all_coords - declared_coords)

    declared_days = {v.get("day") for v in corner.values()}
    declared_days |= {e.get("day") for e in edges}
    declared_days |= {lattice.get("centroid", {}).get("day")}
    available_days = sorted(set(range(27)) - declared_days)

    for coord, day in zip(missing, available_days):
        edges.append(
            {
                "from": "C",
                "to": "C_reset",
                "day": day,
                "letter": "Face",
                "axis": "F",
                "dual_pair": "face_node",
                "coordinate": coord,
                "note": "Declared face node to satisfy 27-node lattice coverage",
            }
        )

    reset = lattice.setdefault("reset_node", {})
    reset["coordinate"] = "333"
    reset["day"] = 25
    reset["note"] = "Out-of-band reset marker; does not collide with corner_map"


def main() -> None:
    text = REGISTRY_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(text)

    # KSR-1.2.0-002: checksum description must describe the product.
    checksum = data.setdefault("checksum_invariant", {})
    checksum["description"] = "Product of Eq6, Eq7 and Eq8 digit-symbol values (336 = 6 * 7 * 8)"

    # KSR-1.2.0-001: quaternary gate primes must match metric_prime_map.
    metric_prime_map_raw: dict[str, int] = data.get("flow_topology", {}).get("metric_prime_map", {})
    metric_prime_map = {k.lower(): v for k, v in metric_prime_map_raw.items()}
    gates = data.setdefault("quaternary_gate_registry", {}).setdefault("gates", {})
    for gate_name, equation_key in (("awareness", "eq6"), ("unity", "eq7"), ("ethics", "eq8")):
        gate = gates.get(gate_name)
        if isinstance(gate, dict):
            gate["prime"] = metric_prime_map.get(equation_key, gate.get("prime"))

    # KSR-1.2.0-004: rename constraint-layer patch day field to commandment_day.
    patches = data.setdefault("constraint_layer_registry", {}).setdefault("patches", {})
    for patch in patches.values():
        if isinstance(patch, dict) and "day" in patch:
            patch["commandment_day"] = patch.pop("day")

    # KSR-1.2.0-008: fix private_paths to actual on-disk paths.
    surface_policy = data.setdefault("surface_policy", {})
    surface_policy["private_paths"] = [
        "apps/backend/backend/kernel/semantic_registry.yaml",
        "apps/backend/backend/kernel/semantic_registry.enc",
        "apps/backend/backend/kernel/.ksr/",
        ".git",
        "__pycache__",
        ".venv",
        "node_modules",
    ]

    # KSR-1.2.0-003: declare face nodes and fix reset node.
    _fix_lattice(data)

    # KSR-1.2.0-009: resolve synonym/symbol ambiguity.
    synonym_registry = data.setdefault("synonym_registry", {})
    if "constraint" in synonym_registry:
        synonym_registry["constraint_term"] = synonym_registry.pop("constraint")
    if "relaxation" in synonym_registry:
        synonym_registry["relaxation_term"] = synonym_registry.pop("relaxation")
    self_ref = synonym_registry.get("self_reference_token")
    if isinstance(self_ref, list) and "I AM loop" in self_ref:
        self_ref.remove("I AM loop")

    # KSR-1.2.0-005 / 006 / 007: regenerate glossary and add steward flags.
    data["glossary"] = _regenerate_glossary(data)

    # KSR-1.2.0-007: add steward_only to P/H confidence nodes in cross_domain_registry.
    cross_domain = data.get("cross_domain_registry")
    if isinstance(cross_domain, dict):
        _walk_add_steward(cross_domain)

    # Dump back with generous width to avoid folding long strings.
    REGISTRY_PATH.write_text(
        yaml.safe_dump(
            data,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
            width=10000,
        ),
        encoding="utf-8",
    )
    print(f"wrote {REGISTRY_PATH}")


if __name__ == "__main__":
    main()
