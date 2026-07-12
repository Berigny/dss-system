"""Relaxation model softening rigid constraint evaluations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RelaxationModel:
    """Simple relaxation model that dampens penalties and rewards alignment."""

    forgiveness_factor: float = 0.2

    def mediate(self, constraint_score: float) -> float:
        """Reduce the raw score to simulate forgiveness."""

        return constraint_score * (1.0 - self.forgiveness_factor)
