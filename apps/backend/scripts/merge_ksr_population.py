#!/usr/bin/env python3
"""Merge populated KSR artifacts into semantic_registry.yaml.

Reads the authoritative private population from
``backend/kernel/.ksr/Kernel/`` and merges it into
``backend/kernel/semantic_registry.yaml``.  Field names are normalised to
match the generator in ``scripts/generate_kernel_constants.py`` while
preserving all steward-only metadata.

Usage:
    python scripts/merge_ksr_population.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).parent.parent
KSR_DIR = REPO_ROOT / "backend" / "kernel"
KSR_YAML = KSR_DIR / "semantic_registry.yaml"
POP_DIR = KSR_DIR / ".ksr" / "Kernel"


def _load_json(name: str) -> dict[str, Any]:
    path = POP_DIR / name
    return json.loads(path.read_text())


def _norm_bridge_edge(edge: dict[str, Any]) -> dict[str, Any]:
    """Normalise bridge-edge field names to match the KSR generator."""
    out = dict(edge)
    if "ternary" in out:
        out["coordinate"] = out.pop("ternary")
    # dual_pair may arrive as "0_4"; expand to "K0_K4".
    dp = out.get("dual_pair", "")
    if isinstance(dp, str) and dp and not dp.startswith("K"):
        parts = dp.split("_")
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            out["dual_pair"] = f"K{parts[0]}_K{parts[1]}"
    return out


def _norm_face_center(face: dict[str, Any]) -> dict[str, Any]:
    """Normalise face-center field names to match the KSR generator."""
    out = dict(face)
    if "ternary" in out:
        out["coordinate"] = out.pop("ternary")
    cf = out.get("cube_face", "")
    if isinstance(cf, str) and "=" in cf:
        out["cube_face"] = cf.replace("=", "")
    return out


def _norm_value_node(meta: dict[str, Any]) -> dict[str, Any]:
    """Normalise value-node field names to match the KSR generator."""
    out = dict(meta)
    if "dimension" in out and "ksr_dimension" not in out:
        out["ksr_dimension"] = out.pop("dimension")
    return out


def _norm_required_dimension(spec: dict[str, Any]) -> dict[str, Any]:
    """Normalise checksum-336 required-dimension field names."""
    out = dict(spec)
    if "valuation" in out and "min_valuation" not in out:
        out["min_valuation"] = out.pop("valuation")
    if "dual_pair" in out and "pair" not in out:
        out["pair"] = out.pop("dual_pair")
    return out


def merge_lattice_registry(ksr: dict[str, Any]) -> None:
    src = _load_json("stage1_lattice_registry.json")["lattice_registry"]
    dst = ksr.setdefault("lattice_registry", {})

    # Scalar metadata.
    for key in (
        "version",
        "cube_id",
        "lattice_type",
        "total_nodes",
        "centroid_coordinate",
        "reset_coordinate",
    ):
        if key in src:
            dst[key] = src[key]

    # Merge corner_map so that existing coordinates are updated but not removed
    # if the population happens to omit one.
    corner_map = dst.setdefault("corner_map", {})
    for coord, meta in src.get("corner_map", {}).items():
        corner_map[coord] = meta

    # Replace structured lists; normalise field names.
    dst["centroid"] = src.get("centroid", dst.get("centroid"))
    dst["reset_node"] = src.get("reset_node", dst.get("reset_node"))
    dst["bridge_edges"] = [_norm_bridge_edge(e) for e in src.get("bridge_edges", [])]
    dst["face_centers"] = [_norm_face_center(f) for f in src.get("face_centers", [])]
    dst["traversal_sequence"] = src.get("traversal_sequence", dst.get("traversal_sequence", []))
    dst["flow_rules"] = src.get("flow_rules", dst.get("flow_rules", []))

    checksum_336 = src.get("checksum_336")
    if checksum_336:
        checksum_336 = dict(checksum_336)
        checksum_336["required_dimensions"] = [
            _norm_required_dimension(spec)
            for spec in checksum_336.get("required_dimensions", [])
        ]
        dst["checksum_336"] = checksum_336
    elif "checksum_336" in dst:
        pass  # keep existing

    dst["seven_cube_expansion"] = src.get("seven_cube_expansion", dst.get("seven_cube_expansion"))


def merge_commandment_patch_registry(ksr: dict[str, Any]) -> None:
    src = _load_json("stage1_commandment_patch.json")["commandment_patch_registry"]
    dst = ksr.setdefault("commandment_patch_registry", {})

    for key in ("version", "source", "encoding_type", "enforcement_mode"):
        if key in src:
            dst[key] = src[key]

    patches = dst.setdefault("patches", {})
    for pid, meta in src.get("patches", {}).items():
        patches[pid] = meta

    if "interaction_matrix" in src:
        dst["interaction_matrix"] = src["interaction_matrix"]
    if "synonym_registry" in src:
        dst["synonym_registry"] = src["synonym_registry"]
    if "ethics_simulation_integration" in src:
        dst["ethics_simulation_integration"] = src["ethics_simulation_integration"]

    # Preserve the corrected 16-bit checksum layout; do not let the populated
    # artifact's 8-bit layout overwrite it.
    layout = dst.setdefault("e6_header_layout", {})
    src_layout = src.get("e6_header_layout", {})
    layout["patch_bits"] = src_layout.get("patch_bits", layout.get("patch_bits", [0, 9]))
    layout["checksum_bits"] = [10, 25]
    layout["reserved_bits"] = [26, 31]


def _norm_balance_rules(rules: dict[str, Any]) -> dict[str, Any]:
    """Normalise value-node balance rules to valid runtime values."""
    out = dict(rules)
    ratio = out.get("max_dominance_ratio")
    if isinstance(ratio, (int, float)) and ratio < 1.0:
        # The populated artifact uses 0.6, which is below the mathematical
        # floor of 1.0 (max >= min). Treat it as a one-decimal-place typo
        # and scale it into the valid range.
        out["max_dominance_ratio"] = ratio * 10
    return out


def merge_value_node_registry(ksr: dict[str, Any]) -> None:
    src = _load_json("stage1_value_nodes_cross_domain.json")["value_node_registry"]
    dst = ksr.setdefault("value_node_registry", {})

    dst["version"] = src.get("version", dst.get("version", "1.0"))
    if "source" in src:
        dst["source"] = src["source"]

    nodes = dst.setdefault("nodes", {})
    for label, meta in src.get("nodes", {}).items():
        nodes[label] = _norm_value_node(meta)

    dst["balance_rules"] = _norm_balance_rules(
        src.get("balance_rules", dst.get("balance_rules", {}))
    )


def merge_cross_domain_registry(ksr: dict[str, Any]) -> None:
    """Assemble cross-domain mappings from the per-domain population files."""
    domains: dict[str, Any] = {}

    mapping_files = {
        "ancient_languages": "xdomain_ancient_languages.json",
        "creative_expressive": "xdomain_creative.json",
        "human_endeavors": "xdomain_human_endeavors.json",
        "kayser_harmonics": "xdomain_kayser_harmonics.json",
        "philosophy_religion": "xdomain_philosophy_religion.json",
        "practical_disciplines": "xdomain_practical.json",
        "science_technology": "xdomain_science_tech.json",
    }

    for domain_name, filename in mapping_files.items():
        data = _load_json(filename)
        # Each file has a single top-level key like "ancient_languages".
        payload = next(iter(data.values()))
        domains[domain_name] = payload

    # The value-node file also carries a cross-domain traversal index.
    value_xdomain = _load_json("stage1_value_nodes_cross_domain.json").get("cross_domain_mappings")

    ksr["cross_domain_registry"] = {
        "version": "1.0",
        "domains": domains,
        "value_node_traversal": value_xdomain,
    }


def merge_ledger_foundation(ksr: dict[str, Any]) -> None:
    """Add the two-layer ledger foundation record as a private KSR section."""
    ksr["ledger_foundation"] = _load_json("ledger_foundation.json")


def merge() -> dict[str, Any]:
    ksr = yaml.safe_load(KSR_YAML.read_text())

    merge_lattice_registry(ksr)
    merge_commandment_patch_registry(ksr)
    merge_value_node_registry(ksr)
    merge_cross_domain_registry(ksr)
    merge_ledger_foundation(ksr)

    return ksr


def main() -> int:
    if not POP_DIR.exists():
        print(f"ERROR: population directory not found: {POP_DIR}", file=sys.stderr)
        return 1

    ksr = merge()
    KSR_YAML.write_text(
        yaml.safe_dump(
            ksr,
            sort_keys=False,
            allow_unicode=True,
            width=120,
            default_flow_style=False,
        )
    )
    print(f"Wrote merged KSR to {KSR_YAML}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
