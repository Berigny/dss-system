#!/usr/bin/env python3
"""
KSR-EVAL DSS-276 — Seed-distribution benchmark artifact for issue #1.

Reruns the LongBench needle harness across the full pinned seed set
(193–197 by default) and emits a ``BenchmarkArtifact`` per whitepaper
Appendix A. The artifact includes per-seed recall, mean, CI95, min/max, and n.

This harness is deterministic and does not call external LLMs, so it is safe
for CI. Hugo is credited as a co-author of the original needle corpus design.

Usage:
    PYTHONPATH=apps/backend python3 tools/seed_distribution_harness.py
    PYTHONPATH=apps/backend python3 tools/seed_distribution_harness.py --seeds 193,194,195
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# The backend benchmark code lives under apps/backend.
_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from backend.benchmarks.artifact_schema import (  # noqa: E402
    BenchmarkArtifact,
    DatasetRef,
    FreshnessInfo,
    HardwareProfile,
    MetricEntry,
    MetricGroup,
    MetricStatistics,
    RepoRef,
)
from backend.benchmarks.hardware import detect_hardware_profile  # noqa: E402
from backend.benchmarks.longbench_needle_benchmark import (  # noqa: E402
    DEFAULT_LENGTHS,
    DEFAULT_TOP_K,
    BenchmarkConfig,
    run_benchmark,
)


DEFAULT_SEEDS = (193, 194, 195, 196, 197)
DEFAULT_OUTPUT_ROOT = Path("eval/reports/benchmarks")


def _repo_sha() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent.parent)
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _compute_statistics(values: list[float]) -> MetricStatistics:
    n = len(values)
    mean = float(statistics.mean(values))
    if n > 1:
        std = float(statistics.stdev(values))
    else:
        std = 0.0
    min_v = float(min(values))
    max_v = float(max(values))
    if n > 1:
        se = std / math.sqrt(n)
        z = 1.959963984540054
        ci_low = mean - z * se
        ci_high = mean + z * se
    else:
        ci_low = ci_high = mean
    return MetricStatistics(
        mean=mean,
        standard_deviation=std,
        min=min_v,
        max=max_v,
        ci_95_low=ci_low,
        ci_95_high=ci_high,
        sample_count=n,
    )


def _build_distribution_artifact(
    *,
    seeds: tuple[int, ...],
    per_seed_recall: dict[int, float],
    qp_recall_at_k: dict[int, float],
    vector_recall_at_1: dict[int, float],
    vector_recall_at_k: dict[int, float],
    repo_sha: str,
    executed_at: datetime,
    runtime_ms: float,
    config: BenchmarkConfig,
) -> BenchmarkArtifact:
    recall_values = [per_seed_recall[s] for s in seeds]
    recall_stats = _compute_statistics(recall_values)

    hardware = detect_hardware_profile()
    return BenchmarkArtifact(
        artefact_schema_version="1.0.0",
        run_id=f"seed-distribution-needle-{executed_at.strftime('%Y%m%dT%H%M%SZ')}",
        suite_id="longbench-needle-seed-distribution",
        suite_version="v1",
        executed_at=executed_at,
        mode="coordinate_guided",
        status="partial",
        repos=[
            RepoRef(
                name="ds-backend-local",
                commit_sha=repo_sha,
                role="canonical_benchmark_engine",
                required_for_run=True,
            )
        ],
        datasets=[
            DatasetRef(
                name="longbench_needle_synthetic_v1",
                version="v1",
                split="benchmark",
                record_count=len(recall_values),
            )
        ],
        metrics={
            "retrieval": MetricGroup(
                status="present",
                metrics={
                    "qp_recall_at_1": MetricEntry(
                        value=recall_stats.mean,
                        unit="ratio",
                        description="Qp needle recall at rank 1 across pinned seeds.",
                        statistics=recall_stats,
                    ),
                    "qp_recall_at_k": MetricEntry(
                        value=float(statistics.mean(qp_recall_at_k.values())),
                        unit="ratio",
                        description=f"Qp needle recall within top {config.top_k} across pinned seeds.",
                    ),
                    "vector_recall_at_1": MetricEntry(
                        value=float(statistics.mean(vector_recall_at_1.values())),
                        unit="ratio",
                        description="Vector-RAG needle recall at rank 1 across pinned seeds.",
                    ),
                    "vector_recall_at_k": MetricEntry(
                        value=float(statistics.mean(vector_recall_at_k.values())),
                        unit="ratio",
                        description=f"Vector-RAG needle recall within top {config.top_k} across pinned seeds.",
                    ),
                    "per_seed_recall": MetricEntry(
                        value=json.dumps({str(s): per_seed_recall[s] for s in seeds}),
                        unit="json",
                        description="Per-seed Qp recall@1 values.",
                    ),
                },
            ),
            "latency": MetricGroup(
                status="present",
                metrics={
                    "total_runtime_ms": MetricEntry(
                        value=runtime_ms,
                        unit="ms",
                        description="Total harness runtime across all seeds.",
                    )
                },
            ),
            "cost": MetricGroup(
                status="present",
                metrics={
                    "embedding_queries": MetricEntry(
                        value=len(recall_values),
                        unit="count",
                        description="Number of seed runs (no LLM tokens consumed).",
                    )
                },
            ),
            "traceability": MetricGroup(
                status="absent",
                absence_reason="Traceability metrics are out of scope for this needle-recall benchmark.",
            ),
            "governance": MetricGroup(
                status="absent",
                absence_reason="Governance metrics are out of scope for this needle-recall benchmark.",
            ),
        },
        freshness=FreshnessInfo(
            status="fresh",
            checked_at=executed_at,
            max_age_hours=24,
            age_hours=0.0,
        ),
        hardware=HardwareProfile(**hardware.to_dict()),
        run_config={
            "lengths": ",".join(str(x) for x in config.lengths),
            "top_k": config.top_k,
            "permutations": config.permutations,
            "seeds": ",".join(str(s) for s in seeds),
            "seed_count": len(seeds),
            "issue": "#1",
            "credit": "Hugo Berigny — original needle corpus design",
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="KSR-EVAL DSS-276 seed distribution harness")
    parser.add_argument(
        "--seeds",
        type=lambda s: tuple(int(x.strip()) for x in s.split(",")),
        default=DEFAULT_SEEDS,
        help="Comma-separated pinned random seeds.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for the distribution artifact.",
    )
    parser.add_argument(
        "--lengths",
        type=lambda s: tuple(int(x.strip()) for x in s.split(",")),
        default=DEFAULT_LENGTHS,
        help="Comma-separated haystack lengths.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help="Top-k cutoff for recall metrics.",
    )
    args = parser.parse_args()

    repo_sha = _repo_sha()
    executed_at = datetime.now(timezone.utc)
    start = time.perf_counter()

    per_seed_recall: dict[int, float] = {}
    qp_recall_at_k: dict[int, float] = {}
    vector_recall_at_1: dict[int, float] = {}
    vector_recall_at_k: dict[int, float] = {}

    config = BenchmarkConfig(
        output_root=Path("/tmp/ksr-seed-distribution"),
        lengths=args.lengths,
        top_k=args.top_k,
        permutations=5_000,
        seed=DEFAULT_SEEDS[0],
    )

    for seed in args.seeds:
        cfg = BenchmarkConfig(
            output_root=config.output_root / str(seed),
            lengths=config.lengths,
            top_k=config.top_k,
            permutations=config.permutations,
            seed=seed,
        )
        summary, _, _ = run_benchmark(cfg)
        per_seed_recall[seed] = summary.qp_recall_at_1
        qp_recall_at_k[seed] = summary.qp_recall_at_k
        vector_recall_at_1[seed] = summary.vector_recall_at_1
        vector_recall_at_k[seed] = summary.vector_recall_at_k
        print(f"seed {seed}: qp_recall@1={summary.qp_recall_at_1:.3f}")

    runtime_ms = (time.perf_counter() - start) * 1000.0

    artifact = _build_distribution_artifact(
        seeds=args.seeds,
        per_seed_recall=per_seed_recall,
        qp_recall_at_k=qp_recall_at_k,
        vector_recall_at_1=vector_recall_at_1,
        vector_recall_at_k=vector_recall_at_k,
        repo_sha=repo_sha,
        executed_at=executed_at,
        runtime_ms=runtime_ms,
        config=config,
    )

    args.output_root.mkdir(parents=True, exist_ok=True)
    output_path = args.output_root / f"seed_distribution_{executed_at.strftime('%Y%m%dT%H%M%SZ')}_{repo_sha[:12]}.json"
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )

    # Emit KSR-EVAL v0.4 manifest.
    from backend.benchmarks.manifest import build_manifest, write_manifest

    manifest = build_manifest(
        artifact,
        eval_script_version="seed_distribution_harness_v1.0",
        seeds=args.seeds,
        conditions={
            "lengths": ",".join(str(x) for x in config.lengths),
            "top_k": config.top_k,
            "permutations": config.permutations,
            "transport": "R1",
            "issue": "#1",
        },
    )
    manifest_path = output_path.with_suffix(".manifest.json")
    write_manifest(manifest, manifest_path)

    print(f"\nSeed distribution artifact: {output_path}")
    print(f"Manifest: {manifest_path}")
    print(f"Mean qp_recall@1: {artifact.metrics['retrieval'].metrics['qp_recall_at_1'].statistics.mean:.3f}")
    print(f"CI95: [{artifact.metrics['retrieval'].metrics['qp_recall_at_1'].statistics.ci_95_low:.3f}, "
          f"{artifact.metrics['retrieval'].metrics['qp_recall_at_1'].statistics.ci_95_high:.3f}]")
    print(f"n={len(args.seeds)} | issue #1 | credit: Hugo Berigny")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
