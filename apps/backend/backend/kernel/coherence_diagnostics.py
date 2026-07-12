"""Cross-domain coherence diagnostics (DS-REVIEW-197).

Lightweight helpers that map the 27-node lattice onto Aboriginal-Dreaming-style
circulation metaphors and report full-coherence status. These are diagnostic
signals only: they feed supplementary context into GovernanceEngine patches
008/009/010 but do not replace the native 336 checksum or ethics calculation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from backend.kernel import constants


# Waterhole nodes in the 27-node traversal: the centroid (Day 13) and the
# wrap/reset point (Day 27, which is the same coordinate as Day 0).
WATERHOLE_INDICES = {13, 27}


@dataclass
class DreamingCheckResult:
    """Result of the full-coherence / dreaming check."""

    coherent: bool
    dual_pairs_synced: bool
    centroid_balanced: bool
    zero_strain: bool
    details: dict[str, Any] = field(default_factory=dict)


def dreaming_check(
    valuations: Mapping[str, int],
    strain: float | None = None,
) -> DreamingCheckResult:
    """Return whether the lattice is in full coherence.

    ``valuations`` maps kernel node IDs (``K0``..``K7`` or ``Eq0``..``Eq9``) to
    integer valuations. The check verifies:

    1. All four S1-S2 dual pairs are synchronised within tolerance.
    2. The centroid primes (137 / 139) are both active.
    3. No strain/deviation is present (``strain`` is None or 0).
    """
    rules = constants.CHECKSUM_336_LATTICE_RULES
    tolerance = int(rules.get("dual_sync_tolerance", 1))
    required = rules.get("required_dimensions", [])
    centroid_primes = rules.get("centroid_active", [137, 139])

    # Normalise valuations to Eq-node names if kernel IDs are supplied.
    norm: dict[str, int] = {}
    for node, val in valuations.items():
        if isinstance(node, str) and node.startswith("K") and node[1:].isdigit():
            eq = constants.LATTICE_CORNER_MAP.get(
                _kernel_id_to_coordinate(node), {}
            ).get("eq_node")
            if eq:
                norm[eq] = int(val)
        else:
            norm[node] = int(val)

    pair_details: list[dict[str, Any]] = []
    dual_pairs_synced = True
    for spec in required:
        pair = spec.get("pair", [])
        if len(pair) != 2:
            continue
        kid_a, kid_b = pair[0], pair[1]
        eq_a = _kernel_id_to_eq_node(kid_a)
        eq_b = _kernel_id_to_eq_node(kid_b)
        v_a = int(norm.get(eq_a, 0)) if eq_a else 0
        v_b = int(norm.get(eq_b, 0)) if eq_b else 0
        min_val = int(spec.get("min_valuation", 0))
        synced = abs(v_a - v_b) <= tolerance and v_a >= min_val and v_b >= min_val
        pair_details.append(
            {
                "pair": f"{kid_a}_{kid_b}",
                "eq_nodes": [eq_a, eq_b],
                "valuations": [v_a, v_b],
                "synced": synced,
            }
        )
        if not synced:
            dual_pairs_synced = False

    # Centroid balance: both Law (137) and Grace (139) traces must be active.
    centroid_active: dict[str, Any] = {}
    centroid_balanced = True
    for prime in centroid_primes:
        eq_node = None
        for node, p in constants.NODE_TO_METRIC_PRIME.items():
            if p == prime:
                eq_node = node
                break
        v = int(norm.get(eq_node, 0)) if eq_node else 0
        active = v > 0
        centroid_active[str(prime)] = {"eq_node": eq_node, "valuation": v, "active": active}
        if not active:
            centroid_balanced = False

    zero_strain = strain is None or float(strain) <= 0.0

    coherent = dual_pairs_synced and centroid_balanced and zero_strain

    return DreamingCheckResult(
        coherent=coherent,
        dual_pairs_synced=dual_pairs_synced,
        centroid_balanced=centroid_balanced,
        zero_strain=zero_strain,
        details={
            "dual_pairs": pair_details,
            "centroid_active": centroid_active,
            "strain": strain,
            "tolerance": tolerance,
        },
    )


class RainbowSerpentCirculation:
    """Circulation tracker for the 27-node lattice traversal.

    The serpent moves through the lattice traversal sequence, wrapping from
    Day 26 back to Day 0. Waterhole nodes are the centroid (Day 13) and the
    reset point (Day 27).
    """

    def __init__(self, position: int = 0) -> None:
        self._sequence = list(constants.LATTICE_TRAVERSAL_SEQUENCE)
        if not self._sequence:
            raise ValueError("Lattice traversal sequence is empty")
        self._position = int(position) % len(self._sequence)

    @property
    def current_position(self) -> int:
        """Return the current day index in the 27-step traversal."""
        return self._position

    @property
    def current_coordinate(self) -> str:
        """Return the ternary coordinate at the current position."""
        return self._sequence[self._position]

    def slither(self, steps: int = 1) -> str:
        """Advance ``steps`` positions and return the new coordinate."""
        self._position = (self._position + int(steps)) % len(self._sequence)
        return self.current_coordinate

    def is_at_waterhole(self) -> bool:
        """Return True if the current position is a waterhole node."""
        return self._position in WATERHOLE_INDICES


def _kernel_id_to_coordinate(kernel_id: str) -> str:
    """Map a kernel ID like ``K0`` to its ternary coordinate."""
    for coord, meta in constants.LATTICE_CORNER_MAP.items():
        if meta.get("kernel") == kernel_id:
            return coord
    return ""


def _kernel_id_to_eq_node(kernel_id: str) -> str | None:
    """Map a kernel ID like ``K0`` to its Eq node."""
    coord = _kernel_id_to_coordinate(kernel_id)
    return constants.LATTICE_CORNER_MAP.get(coord, {}).get("eq_node")
