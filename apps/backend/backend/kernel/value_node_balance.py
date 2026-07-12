"""Lightweight value-node balance diagnostic layer.

Reads the value-node registry from the auto-generated kernel constants and
scores query/context activation across nine value nodes. The result is a
supplementary signal for GovernanceEngine patches 005/008/009/010; it does not
replace native solve_ethics() / equation_9_teleology().
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from backend.kernel import constants
from backend.kernel.user_profile_adapter import UserProfileAdapter


class ValueNodeBalance:
    """Diagnostic balance check over the nine KSR value nodes."""

    NODE_LABELS: tuple[str, ...] = constants.VALUE_NODE_LABELS
    DIMENSIONS: Mapping[str, str] = constants.VALUE_NODE_DIMENSIONS
    PRIME_AFFINITIES: Mapping[str, int] = constants.VALUE_NODE_PRIME_AFFINITIES
    RULES: Mapping[str, Any] = constants.VALUE_NODE_BALANCE_RULES

    def __init__(self) -> None:
        self._min_activation = float(self.RULES["min_activation"])
        self._max_dominance_ratio = float(self.RULES["max_dominance_ratio"])
        self._min_entropy = float(self.RULES["min_entropy"])

    def _default_activation(self) -> float:
        """Fallback activation for nodes not explicitly seeded."""
        return max(self._min_activation, 1e-3)

    def _embedding_projection(self, embedding: np.ndarray, label: str) -> float:
        """Deterministic pseudo-activation from an embedding vector.

        Uses a fixed random direction seeded by the node's prime affinity so the
        same embedding always yields the same score for the same label.
        """
        if embedding.size == 0:
            return self._default_activation()

        rng = np.random.default_rng(seed=int(self.PRIME_AFFINITIES[label]))
        direction = rng.standard_normal(embedding.shape)
        norm = float(np.linalg.norm(direction))
        if norm < 1e-12:
            return self._default_activation()
        direction = direction / norm
        raw = float(np.dot(embedding, direction))
        # Sigmoid to [0, 1] range.
        return float(1.0 / (1.0 + math.exp(-raw)))

    def score(
        self,
        query_embedding: np.ndarray | None = None,
        context: Mapping[str, Any] | None = None,
        profile: str | None = None,
    ) -> dict[str, float]:
        """Return activation scores for each value node.

        Priority:
        1. Explicit ``dimension_scores`` in ``context``.
        2. A provided ``query_embedding``.
        3. Optional user ``profile`` seed from the personality-type overlay.
        4. Uniform default activation.
        """
        context = dict(context) if context else {}
        dim_scores: Mapping[str, float] = context.get("dimension_scores", {})
        embedding = query_embedding

        # Optional personality-type overlay seed. It is applied as a base prior
        # and may be overridden by explicit dimension_scores or embedding.
        profile_seed: dict[str, float] | None = None
        if profile:
            profile_seed = UserProfileAdapter().seed_balance(self, profile)

        scores: dict[str, float] = {}
        for label in self.NODE_LABELS:
            dimension = self.DIMENSIONS[label]
            if dimension in dim_scores:
                scores[label] = float(dim_scores[dimension])
            elif embedding is not None:
                scores[label] = self._embedding_projection(
                    np.asarray(embedding, dtype=float), label
                )
            elif profile_seed is not None:
                scores[label] = float(profile_seed.get(label, self._default_activation()))
            else:
                scores[label] = self._default_activation()

            # Clamp to a sane positive range.
            scores[label] = max(scores[label], 0.0)

        return scores

    def is_balanced(self, scores: Mapping[str, float]) -> tuple[bool, dict[str, float]]:
        """Return (balanced, diagnostics) for the given value-node scores.

        Balance requires:
        - every node has non-zero activation above ``min_activation``;
        - the ratio of max to min activation is below ``max_dominance_ratio``;
        - the distribution entropy is above ``min_entropy``.
        """
        values = [float(scores.get(label, 0.0)) for label in self.NODE_LABELS]
        min_score = min(values)
        max_score = max(values)
        total = sum(values)

        entropy = 0.0
        if total > 0.0:
            probs = [v / total for v in values]
            entropy = -sum(
                p * math.log(p) for p in probs if p > 0.0
            )

        denom = min_score if min_score > 0.0 else 1e-12
        dominance_ratio = max_score / denom

        balanced = (
            min_score > self._min_activation
            and dominance_ratio < self._max_dominance_ratio
            and entropy > self._min_entropy
        )

        diagnostics = {
            "min": min_score,
            "max": max_score,
            "entropy": entropy,
            "dominance_ratio": dominance_ratio,
        }
        return balanced, diagnostics
