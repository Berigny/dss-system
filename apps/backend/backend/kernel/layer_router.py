"""Geological layer router for quaternary-gate evaluated entries.

Routes an entry to Sand, Silt, Loam, or Clay based on the valuations of the
three semantic primes (awareness=5, unity=7, ethics=2). Level 0/1 routes to
Sand, Level 2 routes to Loam, and Level 3 routes to Clay. When gates disagree,
the most restrictive (lowest-position) layer wins.
"""

from __future__ import annotations

from typing import Any, Mapping

from backend.kernel import constants
from backend.kernel.quaternary_gates import QuaternaryGate


class LayerRouter:
    """Assign a geological layer to an entry from quaternary gate levels."""

    # Map from quaternary level key to geological layer.
    _LEVEL_TO_LAYER: Mapping[str, str] = {
        "level_0": constants.LAYER_SAND,  # Gate collapse: forensic Sand only.
        "level_1": constants.LAYER_SAND,
        "level_2": constants.LAYER_LOAM,
        "level_3": constants.LAYER_CLAY,
    }

    # Canonical geological layer -> provenance retention tier.
    _LAYER_TO_RETENTION_TIER: Mapping[str, str] = {
        constants.LAYER_SAND: "Sand",
        constants.LAYER_SILT: "Silt",
        constants.LAYER_LOAM: "Loam",
        constants.LAYER_CLAY: "Clay",
    }

    # Geological layer -> candidate surface tier vocabulary used by the
    # orchestrator and middleware (tier_rank 0..3, relevance_tier 1..4).
    _LAYER_TO_CANDIDATE_TIERS: Mapping[str, Mapping[str, int]] = {
        constants.LAYER_SAND: {"tier_rank": 0, "relevance_tier": 4},
        constants.LAYER_SILT: {"tier_rank": 1, "relevance_tier": 3},
        constants.LAYER_LOAM: {"tier_rank": 2, "relevance_tier": 2},
        constants.LAYER_CLAY: {"tier_rank": 3, "relevance_tier": 1},
    }

    @classmethod
    def route(cls, entry: Mapping[str, Any]) -> str:
        """Return the geological layer for ``entry``.

        ``entry`` must contain integer valuations:
            ``v_awareness``, ``v_unity``, ``v_ethics``.

        The returned layer is one of ``LAYER_SAND``, ``LAYER_SILT``,
        ``LAYER_LOAM``, or ``LAYER_CLAY``. SILT is reserved for decayed Loam
        and is never the initial route.
        """
        result = QuaternaryGate.evaluate(
            entry.get("v_awareness"),
            entry.get("v_unity"),
            entry.get("v_ethics"),
        )

        # If all gates are Level 3, short-circuit to Clay.
        if result["clay_admissible"]:
            return constants.LAYER_CLAY

        # Otherwise return the most restrictive layer among the three gates.
        order = constants.QUATERNARY_LAYER_ORDER
        layers = [cls._LEVEL_TO_LAYER[level] for level in result["levels"].values()]
        return min(layers, key=lambda layer: order.index(layer))

    @classmethod
    def route_from_levels(cls, levels: Mapping[str, str]) -> str:
        """Route from an explicit ``{gate: level_key}`` mapping."""
        if all(level == "level_3" for level in levels.values()):
            return constants.LAYER_CLAY
        order = constants.QUATERNARY_LAYER_ORDER
        layers = [cls._LEVEL_TO_LAYER[level] for level in levels.values()]
        return min(layers, key=lambda layer: order.index(layer))

    @classmethod
    def layer_to_retention_tier(cls, layer: str) -> str:
        """Return the provenance retention tier name for a geological layer."""
        return cls._LAYER_TO_RETENTION_TIER.get(layer, "Clay")

    @classmethod
    def layer_to_candidate_tiers(cls, layer: str) -> Mapping[str, int]:
        """Return ``{tier_rank, relevance_tier}`` for a geological layer."""
        return dict(cls._LAYER_TO_CANDIDATE_TIERS.get(layer, {"tier_rank": 0, "relevance_tier": 4}))
