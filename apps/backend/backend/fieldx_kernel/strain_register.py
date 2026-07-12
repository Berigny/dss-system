"""Register used to accumulate deformation or strain signals."""

from __future__ import annotations

from typing import Dict, List


class StrainRegister:
    """Collects strain readings and exposes simple aggregation helpers."""

    def __init__(self) -> None:
        self._readings: Dict[str, List[float]] = {}

    def record(self, channel: str, value: float) -> None:
        """Record a strain value for the given channel."""

        self._readings.setdefault(channel, []).append(value)

    def average(self, channel: str) -> float:
        """Return the average strain for the channel or zero if empty."""

        values = self._readings.get(channel, [])
        if not values:
            return 0.0
        return sum(values) / len(values)

    def reset(self) -> None:
        """Clear all recorded strain values."""

        self._readings.clear()
