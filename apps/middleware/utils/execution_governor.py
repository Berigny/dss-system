"""Adaptive execution governor for middleware request shaping."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionDecision:
    profile: str
    pressure: float
    allow_assemble: bool
    max_decoded_coords: int
    backend_enable_ledger: bool
    defer_guardian: bool
    reason: str


class ExecutionGovernor:
    def __init__(
        self,
        *,
        enabled: bool,
        force_profile: str = "",
        local_provider_markers: tuple[str, ...] = ("ollama", "llama", "local"),
    ) -> None:
        self.enabled = bool(enabled)
        self.force_profile = (force_profile or "").strip().upper()
        self.local_provider_markers = tuple(
            marker.strip().lower() for marker in local_provider_markers if marker and marker.strip()
        )

    def is_local_provider(self, provider: str) -> bool:
        p = (provider or "").strip().lower()
        if not p:
            return False
        return any(marker in p for marker in self.local_provider_markers)

    @staticmethod
    def _load_pressure() -> float:
        try:
            load1, _, _ = os.getloadavg()
            cores = os.cpu_count() or 1
            if cores <= 0:
                cores = 1
            return max(0.0, min(100.0, (float(load1) / float(cores)) * 100.0))
        except Exception:
            return 0.0

    def _choose_profile(self, pressure: float) -> str:
        if self.force_profile in {"FULL", "FAST", "MINIMAL"}:
            return self.force_profile
        if pressure >= 70.0:
            return "MINIMAL"
        if pressure >= 35.0:
            return "FAST"
        return "FULL"

    def decide(
        self,
        *,
        provider: str,
        enable_ledger: bool,
        network_pressure_hint: float | None = None,
    ) -> ExecutionDecision:
        if not self.enabled:
            return ExecutionDecision(
                profile="FULL",
                pressure=0.0,
                allow_assemble=True,
                max_decoded_coords=18,
                backend_enable_ledger=bool(enable_ledger),
                defer_guardian=False,
                reason="adaptive_disabled",
            )

        local = self.is_local_provider(provider)
        pressure = self._load_pressure()
        if local:
            pressure = min(100.0, pressure + 15.0)

        if isinstance(network_pressure_hint, (int, float)):
            pressure = max(pressure, max(0.0, min(100.0, float(network_pressure_hint))))

        profile = self._choose_profile(pressure)
        if profile == "MINIMAL":
            return ExecutionDecision(
                profile=profile,
                pressure=pressure,
                allow_assemble=False,
                max_decoded_coords=0,
                backend_enable_ledger=False,
                defer_guardian=bool(enable_ledger),
                reason="high_pressure_minimal",
            )

        if profile == "FAST":
            allow_assemble = not local
            return ExecutionDecision(
                profile=profile,
                pressure=pressure,
                allow_assemble=allow_assemble,
                max_decoded_coords=4,
                backend_enable_ledger=(bool(enable_ledger) and not local),
                defer_guardian=(bool(enable_ledger) and local),
                reason="moderate_pressure_fast",
            )

        return ExecutionDecision(
            profile="FULL",
            pressure=pressure,
            allow_assemble=True,
            max_decoded_coords=18,
            backend_enable_ledger=bool(enable_ledger),
            defer_guardian=False,
            reason="low_pressure_full",
        )


__all__ = ["ExecutionGovernor", "ExecutionDecision"]
