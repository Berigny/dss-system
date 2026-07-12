"""Coordinate-sequence embeddings for the 27-node kernel lattice.

This module provides a lightweight, dependency-free embedding function that
maps a sequence of lattice coordinates to a fixed 12-dimensional vector.  It
is intended for structural similarity comparisons only, not for semantic
representation.
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

from backend.kernel.coord_enrichment import COORD_REGISTRY


def _safe_card(coord: str):
    return COORD_REGISTRY.get(coord)


def coord_sequence_embedding(
    coord_sequence: Sequence[str],
    normalize: bool = True,
) -> Tuple[float, ...]:
    """Return a 12-dimensional embedding vector for ``coord_sequence``.

    The vector encodes coverage ratios across the three structural dimensions
    (layer, mode, breath), centroid activation, elemental diversity, and the
    density of cross-substrate transitions.  It is deterministic and uses only
    the coordinate enrichment registry.

    Args:
        coord_sequence: Ordered list of coordinate ids, e.g. ``("000", "001")``.
        normalize: If ``True`` (default), coverage ratios are divided by the
            sequence length so that sequences of different lengths are
            comparable.

    Returns:
        A 12-tuple of floats.
    """
    n = len(coord_sequence)
    if n == 0:
        return tuple([0.0] * 12)

    layer_counts = {0: 0, 1: 0, 2: 0}
    mode_counts = {0: 0, 1: 0, 2: 0}
    breath_counts = {0: 0, 1: 0, 2: 0}
    elements: set[str] = set()
    centroid_present = 0
    valid_coords: list[str] = []

    for coord in coord_sequence:
        card = _safe_card(coord)
        if card is None:
            continue
        valid_coords.append(coord)
        layer_counts[card.layer] = layer_counts.get(card.layer, 0) + 1
        mode_counts[card.mode] = mode_counts.get(card.mode, 0) + 1
        breath_counts[card.breath] = breath_counts.get(card.breath, 0) + 1
        if card.element:
            elements.add(card.element)
        if card.coord_id == "111":
            centroid_present = 1

    m = max(len(valid_coords), 1)

    # Count S1/S2 cross-substrate transitions.
    transitions = 0
    for a, b in zip(valid_coords, valid_coords[1:]):
        card_a = _safe_card(a)
        card_b = _safe_card(b)
        if card_a is None or card_b is None:
            continue
        tetra_a = card_a.tetrahedron
        tetra_b = card_b.tetrahedron
        if tetra_a in {"S1", "S2"} and tetra_b in {"S1", "S2"} and tetra_a != tetra_b:
            transitions += 1

    def ratio(counts: dict[int, int], key: int) -> float:
        value = counts.get(key, 0)
        return value / m if normalize else float(value)

    vector = [
        ratio(layer_counts, 0),
        ratio(layer_counts, 1),
        ratio(layer_counts, 2),
        ratio(mode_counts, 0),
        ratio(mode_counts, 1),
        ratio(mode_counts, 2),
        ratio(breath_counts, 0),
        ratio(breath_counts, 1),
        ratio(breath_counts, 2),
        float(centroid_present),
        len(elements) / 6.0,
        transitions / max(m - 1, 1) if normalize else transitions,
    ]
    return tuple(vector)


def embedding_similarity(
    a: Sequence[float],
    b: Sequence[float],
) -> float:
    """Return the cosine similarity between two embedding vectors.

    Returns ``0.0`` if either vector has zero magnitude.
    """
    if len(a) != len(b):
        raise ValueError("Embedding vectors must have the same length")

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    similarity = dot / (norm_a * norm_b)
    # Guard against tiny floating-point overshoot.
    return max(-1.0, min(1.0, similarity))


__all__ = (
    "coord_sequence_embedding",
    "embedding_similarity",
)
