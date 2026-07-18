"""Consilience benchmarks for the Kernel Semantic Registry.

These tests make the kernel's structural claims falsifiable by:
- Auditing confidence/relation metadata on KSR entries.
- Running permutation controls on the 3-bit pattern ↔ kernel corner bijection.
- Verifying that the public constants module carries the KSR scope statement.
"""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path
from typing import Any

# Kernel corner coordinates are ternary triples (L, M, B) where each coordinate
# is either 0 or 2.  The comparison patterns are binary triples (bottom, middle,
# top).  The structural claim is that both are 3-bit patterns and therefore
# admit a bijection; any random permutation should destroy adjacency structure.
CORNERS = [
    (0, 0, 0),  # K0
    (0, 0, 2),  # K1
    (0, 2, 0),  # K2
    (0, 2, 2),  # K3
    (2, 0, 0),  # K4
    (2, 0, 2),  # K5
    (2, 2, 0),  # K6
    (2, 2, 2),  # K7
]

PATTERNS = [
    (0, 0, 0),
    (0, 0, 1),
    (0, 1, 0),
    (0, 1, 1),
    (1, 0, 0),
    (1, 0, 1),
    (1, 1, 0),
    (1, 1, 1),
]


def _adjacent_pairs(points: list[tuple[int, ...]]) -> set[tuple[int, int]]:
    """Return unordered pairs of indices that differ in exactly one bit/ternary slot."""
    pairs: set[tuple[int, int]] = set()
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            diffs = sum(a != b for a, b in zip(points[i], points[j]))
            if diffs == 1:
                pairs.add((i, j))
    return pairs


def _mapping_score(mapping: list[int]) -> int:
    """Count adjacent kernel corners that map to adjacent comparison patterns."""
    corner_adj = _adjacent_pairs(CORNERS)
    pattern_adj = _adjacent_pairs(PATTERNS)
    score = 0
    for i, j in corner_adj:
        if (mapping[i], mapping[j]) in pattern_adj:
            score += 1
    return score


def run_iching_permutation_test(iterations: int = 1000, seed: int = 42) -> dict[str, Any]:
    """Null-hypothesis test for the kernel-corner / 3-bit-pattern bijection.

    The structural mapping is the identity ordering (corner i maps to pattern i).
    We compare its adjacency-preservation score to 1000 random permutations.
    """
    rng = random.Random(seed)
    structural_mapping = list(range(8))
    structural_score = _mapping_score(structural_mapping)

    random_scores = []
    for _ in range(iterations):
        perm = structural_mapping.copy()
        rng.shuffle(perm)
        random_scores.append(_mapping_score(perm))

    better_or_equal = sum(1 for s in random_scores if s >= structural_score)
    p_value = better_or_equal / iterations

    return {
        "structural_score": structural_score,
        "mean_random_score": sum(random_scores) / len(random_scores),
        "max_random_score": max(random_scores),
        "p_value": p_value,
        "passes": structural_score > max(random_scores),
    }


def run_confidence_audit(ksr_data: dict[str, Any]) -> dict[str, Any]:
    """Count confidence/relation labels in the KSR glossary and cross-domain registry."""
    glossary_counts = Counter()
    for entry in ksr_data.get("glossary", []):
        glossary_counts[entry.get("confidence", "?")] += 1

    cross_domain_counts = Counter()
    cdr = ksr_data.get("cross_domain_registry", {})
    for domain, days in cdr.get("domains", {}).items():
        for day, languages in days.items():
            if not isinstance(languages, dict):
                continue
            for _lang, mapping in languages.items():
                if isinstance(mapping, dict):
                    cross_domain_counts[mapping.get("confidence", "?")] += 1

    return {
        "glossary_confidence_counts": dict(glossary_counts),
        "cross_domain_confidence_counts": dict(cross_domain_counts),
        "has_scope_statement": bool(ksr_data.get("ksr_scope_statement")),
        "has_confidence_taxonomy": bool(ksr_data.get("confidence_taxonomy")),
        "has_relation_types": bool(ksr_data.get("relation_types")),
    }


def run_constants_scope_check(repo_root: Path | str = ".") -> dict[str, Any]:
    """Verify the public constants module carries the KSR scope statement."""
    constants_path = Path(repo_root) / "backend" / "kernel" / "constants.py"
    source = constants_path.read_text(encoding="utf-8")
    return {
        "scope_statement_present": "KSR_SCOPE_STATEMENT" in source,
        "confidence_taxonomy_present": "CONFIDENCE_TAXONOMY" in source,
        "relation_types_present": "RELATION_TYPES" in source,
    }
