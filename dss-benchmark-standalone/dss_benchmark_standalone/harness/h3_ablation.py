"""H3 efficiency-floor ablation harness.

Generates synthetic telemetry samples, runs them through the 336 Lawfulness
Gateway in ``enabled`` and ``disabled`` modes, and reports rejection rates.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make dss-codebase packages importable without installation.
_DSS_CODEBASE = Path(__file__).resolve().parents[4] / "dss-codebase"
for _root in (
    _DSS_CODEBASE / "packages" / "gate",
    _DSS_CODEBASE / "packages" / "flow",
    _DSS_CODEBASE / "packages" / "ledger",
    _DSS_CODEBASE / "apps" / "backend",
):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from dss_flow.telemetry import TelemetrySample  # noqa: E402
from dss_gate.gateway import LawfulnessGateway  # noqa: E402


THETA_A = 0.20


def make_telemetry(
    cpu_s: float,
    gpu_s: float,
    tokens_in: int = 100,
    tokens_out: int = 100,
    wall_ms: float = 100.0,
    joules: float | None = 1.0,
    model: str = "synthetic-model",
    task_class: str = "synthetic-task",
) -> TelemetrySample:
    """Build a deterministic TelemetrySample for the ablation."""
    return TelemetrySample(
        cpu_s=cpu_s,
        gpu_s=gpu_s,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        wall_ms=wall_ms,
        joules=joules,
        model=model,
        task_class=task_class,
    )


def _gateway_context() -> dict[str, Any]:
    """Return a context that lets quaternary and patch surfaces pass."""
    return {"v_awareness": 6, "v_unity": 6, "v_ethics": 6}


@dataclass
class AblationResult:
    """Result of an H3 ablation run."""

    rejection_rate_on: float
    rejection_rate_off: float
    causal_delta: float
    sub_threshold_count: int
    supra_threshold_count: int
    all_fingerprints_have_efficiency_eta: bool


def generate_samples(
    n_sub: int = 20,
    n_supra: int = 20,
    baseline: float = 1.0,
    seed: int = 42,
) -> tuple[list[TelemetrySample], list[TelemetrySample]]:
    """Generate sub-threshold and supra-threshold telemetry samples."""
    import random

    rng = random.Random(seed)
    # E_pred for default sample = cpu+gpu+0.001*(in+out)+0.0001*wall + joules*0.1
    # baseline=1.0, theta=0.20 => need E_pred > 0.8 for sub, <= 0.8 for supra.
    sub_samples: list[TelemetrySample] = []
    for i in range(n_sub):
        scale = 1.0 + rng.uniform(0.0, 0.5)
        sub_samples.append(
            make_telemetry(
                cpu_s=0.4 * scale,
                gpu_s=0.4 * scale,
                joules=2.0 * scale,
                wall_ms=200.0 + i,
            )
        )
    supra_samples: list[TelemetrySample] = []
    for i in range(n_supra):
        scale = 0.05 + rng.uniform(0.0, 0.05)
        supra_samples.append(
            make_telemetry(
                cpu_s=0.05 * scale,
                gpu_s=0.05 * scale,
                joules=0.1 * scale,
                wall_ms=50.0 + i,
            )
        )
    return sub_samples, supra_samples


def run_ablation(
    sub_samples: list[TelemetrySample],
    supra_samples: list[TelemetrySample],
    baseline: float = 1.0,
) -> AblationResult:
    """Run the gateway on/off for the same samples and return metrics."""
    gateway_on = LawfulnessGateway(enabled=True)
    gateway_off = LawfulnessGateway(enabled=False)
    context = _gateway_context()

    all_samples = list(sub_samples) + list(supra_samples)

    rejected_on = 0
    rejected_off = 0
    all_have_eta = True

    for i, sample in enumerate(all_samples):
        evidence_cid = f"sha256:ablation:{i:04d}"
        verdict_on = gateway_on.evaluate_append(
            evidence_cid=evidence_cid,
            context=context,
            telemetry_sample=sample,
            baseline=baseline,
        )
        verdict_off = gateway_off.evaluate_append(
            evidence_cid=evidence_cid,
            context=context,
            telemetry_sample=sample,
            baseline=baseline,
        )
        if not verdict_on.pass_:
            rejected_on += 1
        if not verdict_off.pass_:
            rejected_off += 1
        if "efficiency_eta" not in verdict_on.fingerprint:
            all_have_eta = False
        if "efficiency_eta" not in verdict_off.fingerprint:
            all_have_eta = False

    total = len(all_samples)
    rejection_rate_on = rejected_on / total if total else 0.0
    rejection_rate_off = rejected_off / total if total else 0.0
    return AblationResult(
        rejection_rate_on=rejection_rate_on,
        rejection_rate_off=rejection_rate_off,
        causal_delta=rejection_rate_on - rejection_rate_off,
        sub_threshold_count=len(sub_samples),
        supra_threshold_count=len(supra_samples),
        all_fingerprints_have_efficiency_eta=all_have_eta,
    )
