from __future__ import annotations

from typing import Dict

CODATA_ALPHA_INV: float = 137.035999177  # CODATA 2022 inverse fine-structure constant


def compute_delta_sub(
    alpha_computed: float,
    alpha_experimental: float = CODATA_ALPHA_INV,
) -> float:
    """Residual δ_sub = α⁻¹_exp - (1 / α_computed)."""
    if alpha_computed == 0.0:
        raise ValueError("Computed alpha cannot be zero")
    theory_inv = 1.0 / alpha_computed
    return alpha_experimental - theory_inv


def correlate_residual_to_sim_metrics(
    alpha_computed: float,
    avg_coherence_drift: float,
    flow_violation_rate: float,
    num_cycles: int = 1000,
    drift_scale: float = 0.05,
    violation_scale: float = 0.002,
) -> Dict[str, float]:
    delta = compute_delta_sub(alpha_computed)
    abs_delta = abs(delta)

    drift_contrib = abs(avg_coherence_drift) * drift_scale
    violation_contrib = abs(flow_violation_rate) * violation_scale

    explained = drift_contrib + violation_contrib
    explained_fraction = min(1.0, explained / max(abs_delta, 1e-10))

    unexplained = max(0.0, abs_delta - explained)

    return {
        "delta_sub": delta,
        "abs_delta": abs_delta,
        "coherence_drift_contrib": drift_contrib,
        "flow_violation_contrib": violation_contrib,
        "explained_fraction": explained_fraction,
        "unexplained_ppm": unexplained * 1e6,
        "cycles": num_cycles,
    }
