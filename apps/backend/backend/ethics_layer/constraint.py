"""Constraint model capturing rigid evaluation criteria."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from backend.fieldx_kernel.models import ContinuousState


@dataclass
class Constraint:
    """Declarative policy that scores a continuous state."""

    name: str
    weight: float = 1.0

    def evaluate(self, state: ContinuousState) -> float:
        """Return a score proportional to the configured weight."""

        if not isinstance(state, ContinuousState):
            return 0.0
        magnitude = sum(abs(val) for val in state.coordinates.values())
        return self.weight * magnitude
