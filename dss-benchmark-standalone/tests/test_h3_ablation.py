"""H3 efficiency-floor ablation test.

Proves that the 336 gateway rejects sub-threshold commits (A_corr < 0.20)
and that disabling the gate materially lowers the rejection rate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the benchmark package is importable and can reach dss-codebase.
_BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCHMARK_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCHMARK_ROOT))

from dss_benchmark_standalone.harness.h3_ablation import (  # noqa: E402
    generate_samples,
    run_ablation,
)


def test_sub_threshold_rejected_when_enabled_and_accepted_when_disabled() -> None:
    """EFF-05: sub-threshold commits are rejected with the gate on and pass with it off."""
    sub_samples, supra_samples = generate_samples(n_sub=20, n_supra=20)
    result = run_ablation(sub_samples, supra_samples)

    # The causal delta should be large: the gate causes rejections.
    assert result.causal_delta > 0.3, (
        f"causal_delta {result.causal_delta} is too small; gate did not cause rejections"
    )
    # With the gate off, nearly all samples pass.
    assert result.rejection_rate_off < 0.10, (
        f"rejection_rate_off {result.rejection_rate_off} is too high"
    )
    # With the gate on, a substantial share is rejected.
    assert result.rejection_rate_on > 0.30, (
        f"rejection_rate_on {result.rejection_rate_on} is too low"
    )


def test_efficiency_eta_in_every_fingerprint() -> None:
    """EFF-04: every Closure Fingerprint issued during ablation carries efficiency_eta."""
    sub_samples, supra_samples = generate_samples(n_sub=10, n_supra=10)
    result = run_ablation(sub_samples, supra_samples)
    assert result.all_fingerprints_have_efficiency_eta


def test_same_sample_same_outcome_across_modes() -> None:
    """Same telemetry yields deterministic pass/fail in each mode."""
    sub_samples, supra_samples = generate_samples(n_sub=5, n_supra=5)
    result1 = run_ablation(sub_samples, supra_samples)
    result2 = run_ablation(sub_samples, supra_samples)
    assert result1.rejection_rate_on == pytest.approx(result2.rejection_rate_on)
    assert result1.rejection_rate_off == pytest.approx(result2.rejection_rate_off)
