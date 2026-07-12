"""Lightweight hysteresis placeholder to smooth value changes."""

from __future__ import annotations

from typing import Optional


class HysteresisModel:
    """Applies a simple threshold-based hysteresis to incoming signals."""

    def __init__(self, activation_threshold: float = 0.1) -> None:
        self.activation_threshold = activation_threshold
        self._previous: Optional[float] = None

    def apply(self, value: float) -> float:
        """Return the stabilized value based on the configured threshold."""

        if self._previous is None:
            self._previous = value
            return value

        if abs(value - self._previous) >= self.activation_threshold:
            self._previous = value
        return self._previous
