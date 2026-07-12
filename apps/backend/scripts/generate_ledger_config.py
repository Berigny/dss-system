#!/usr/bin/env python3
"""Build-time generator for the kimi-ledger process-layer config.

Reads the already-stripped public kernel constants and emits the four config
files required by the dual-layer non-commutative ledger:

    kimi-ledger/config/ontology.json
    kimi-ledger/config/slots.json
    kimi-ledger/config/relations.json
    kimi-ledger/config/weights.json

The kernel remains the single source of truth for primes, patches, and
value-node balance rules. This script must be re-run whenever the kernel
constants change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.kernel import constants


def _prime_pool(start: int) -> list[int]:
    """Return an infinite-ish pool of candidate primes >= start."""
    candidates: list[int] = []
    for n in range(start, 10_000):
        if n < 2:
            continue
        for p in range(2, int(n**0.5) + 1):
            if n % p == 0:
                break
        else:
            candidates.append(n)
    return candidates


def generate(repo_root: Path) -> dict[str, Any]:
    quaternary_primes = set(constants.QUATERNARY_GATE_TO_PRIME.values())
    slot_bases = [2, 3, 5, 7, 11]
    reserved_for_slots = set(slot_bases)

    # Relation primes are drawn from a high range so they cannot collide with
    # ontology or slot primes.
    relation_pool = _prime_pool(83)
    relations: dict[str, dict[str, Any]] = {}
    for patch_id in sorted(constants.PATCH_REGISTRY):
        meta = constants.PATCH_REGISTRY[patch_id]
        relations[patch_id] = {
            "prime": relation_pool.pop(0),
            "engineering_replacement": meta.get("engineering_replacement", patch_id),
            "category": meta.get("category", "unknown"),
            "hard_gate": meta.get("hard_gate", "Operation blocked."),
        }
    reserved_for_relations = {r["prime"] for r in relations.values()}

    # Ontology concepts are derived from value-node labels and engineering
    # dimensions, then assigned disjoint process primes.
    concept_sources: dict[str, int] = {}
    for label in constants.VALUE_NODE_LABELS:
        concept_sources[label] = constants.VALUE_NODE_PRIME_AFFINITIES[label]
    for prime, dimension in constants.PRIME_TO_DIMENSION.items():
        if dimension not in concept_sources:
            concept_sources[dimension] = prime

    excluded = quaternary_primes | reserved_for_slots | reserved_for_relations
    ontology_pool = [p for p in _prime_pool(13) if p not in excluded]
    ontology: dict[str, dict[str, Any]] = {}
    for name in sorted(concept_sources):
        ontology[name] = {
            "prime": ontology_pool.pop(0),
            "kernel_prime": concept_sources[name],
            "domain": constants.VALUE_NODE_DIMENSIONS.get(name, "kernel"),
            "type": "value_node" if name in constants.VALUE_NODE_LABELS else "dimension",
        }

    slots = {
        "meta": {
            "layer": "process",
            "note": "Fixed positional bases encode syntax; exponents encode concepts.",
        },
        "slots": {
            "agent": {"base": 2, "description": "Who/What acts"},
            "verb": {"base": 3, "description": "What happens"},
            "patient": {"base": 5, "description": "What is acted upon"},
            "result": {"base": 7, "description": "What is produced"},
            "context": {"base": 11, "description": "Where/When/How"},
        },
    }

    weights = {
        "meta": {
            "source": "VALUE_NODE_BALANCE_RULES",
            "purpose": "Decay/activation/certainty weights for process validation.",
        },
        "rules": dict(constants.VALUE_NODE_BALANCE_RULES),
    }

    return {
        "ontology.json": {
            "meta": {
                "version": constants.KSR_VERSION,
                "layer": "ontology",
                "note": "Concepts are derived from kernel value-node and dimension registries; process primes are disjoint from quaternary and slot primes.",
            },
            "concepts": ontology,
        },
        "slots.json": slots,
        "relations.json": {
            "meta": {
                "layer": "process",
                "purpose": "Hard-gate relation markers derived from the kernel patch registry.",
            },
            "relations": relations,
        },
        "weights.json": weights,
    }


def main() -> int:
    repo_root = Path(__file__).parent.parent
    config_dir = repo_root / "dss_ledger" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    outputs = generate(repo_root)
    for filename, data in outputs.items():
        (config_dir / filename).write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n"
        )
        print(f"Wrote {config_dir / filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
