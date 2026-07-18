#!/usr/bin/env python3
"""
KSR-EVAL DSS-276 — Seed-distribution benchmark artifact for issue #1.

Reruns the LongBench needle harness across the full pinned seed set
(193–197 by default) and emits a ``BenchmarkArtifact`` per whitepaper
Appendix A. The artifact includes per-seed Qp and vector recall, mean, CI95,
min/max, and n, plus B3 matched-information baselines on the same seeds.

This harness is deterministic and does not call external LLMs, so it is safe
for CI. Credit: hugooconnor for issue #1 reproduction and critique.

Usage:
    PYTHONPATH=apps/backend python3 tools/seed_distribution_harness.py
    PYTHONPATH=apps/backend python3 tools/seed_distribution_harness.py --seeds 193,194,195
"""

from __future__ import annotations

import argparse
import hashlib
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
from backend.benchmarks.comparison_baselines import BASELINES  # noqa: E402
from backend.benchmarks.hardware import detect_hardware_profile  # noqa: E402
from backend.benchmarks.longbench_needle_benchmark import (  # noqa: E402
    DEFAULT_LENGTHS,
    DEFAULT_TOP_K,
    BenchmarkConfig,
    NeedleMemory,
    NeedleQuery,
    generate_corpus,
    evaluate,
)
from backend.benchmarks.manifest import build_manifest, write_manifest  # noqa: E402
from backend.benchmarks.metadata_filter_baseline import MetadataFilterBaseline  # noqa: E402
from backend.benchmarks.real_embedding_baseline import (  # noqa: E402
    PINNED_MODEL_NAME,
    RealEmbeddingBaseline,
)


DEFAULT_SEEDS = (193, 194, 195, 196, 197)
DEFAULT_OUTPUT_ROOT = Path("eval/reports/benchmarks")
DATASET_NAME = "longbench_needle_synthetic_v1"


def _repo_sha() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent.parent)
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _dataset_sha256(lengths: tuple[int, ...], seeds: tuple[int, ...]) -> str:
    """Deterministic fingerprint of the synthetic corpus generator inputs."""
    payload = json.dumps({"name": DATASET_NAME, "lengths": lengths, "seeds": list(seeds)}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compute_statistics(values: list[float]) -> MetricStatistics:
    n = len(values)
    mean = float(statistics.mean(values))
    std = float(statistics.stdev(values)) if n > 1 else 0.0
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


def _normalize_for_b3(
    memories: list[NeedleMemory],
    queries: list[NeedleQuery],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize needle corpus for the comparison-baseline interface."""
    norm_memories = [
        {
            "id": str(m.memory_id),
            "text": str(m.text),
            "coordinate": m.coordinate,
        }
        for m in memories
    ]
    norm_queries = [
        {
            "id": str(q.query_id),
            "text": str(q.text),
            "relevant_ids": {str(q.needle_id)},
            "coordinate": q.coordinate,
        }
        for q in queries
    ]
    return norm_memories, norm_queries


def _run_b3_baselines(
    memories: list[NeedleMemory],
    queries: list[NeedleQuery],
    top_k: int,
) -> dict[str, Any]:
    """Run B3 matched-information baselines on one seed's corpus."""
    norm_memories, norm_queries = _normalize_for_b3(memories, queries)

    bow = BASELINES["bow_stand_in"]
    bow_result = bow.run(norm_memories, norm_queries, top_k=top_k)

    metadata = MetadataFilterBaseline()
    metadata_result = metadata.run(norm_memories, norm_queries, top_k=top_k)

    real_embedding: dict[str, Any] = {"available": False, "reason": "sentence-transformers not available"}
    try:
        real_emb = RealEmbeddingBaseline()
        real_result = real_emb.run(norm_memories, norm_queries, top_k=top_k)
        real_embedding = {
            "available": True,
            "model": PINNED_MODEL_NAME,
            "weights_sha256": real_emb.model_info.weights_sha256 if real_emb.model_info else None,
            "recall_at_1": real_result.recall_at_1,
            "recall_at_k": real_result.recall_at_k,
            "mrr": real_result.mrr,
        }
    except Exception as exc:
        real_embedding["reason"] = str(exc)

    return {
        "bow_stand_in_recall_at_1": bow_result.recall_at_1,
        "bow_stand_in_recall_at_k": bow_result.recall_at_k,
        "metadata_filter_recall_at_1": metadata_result.recall_at_1,
        "metadata_filter_recall_at_k": metadata_result.recall_at_k,
        "real_embedding": real_embedding,
    }


def _build_distribution_artifact(
    *,
    seeds: tuple[int, ...],
    per_seed: dict[int, dict[str, float]],
    b3_per_seed: dict[int, dict[str, Any]],
    repo_sha: str,
    executed_at: datetime,
    runtime_ms: float,
    config: BenchmarkConfig,
) -> BenchmarkArtifact:
    qp_recall_values = [per_seed[s]["qp_recall_at_1"] for s in seeds]
    qp_recall_stats = _compute_statistics(qp_recall_values)

    vector_recall_values = [per_seed[s]["vector_recall_at_1"] for s in seeds]
    vector_recall_stats = _compute_statistics(vector_recall_values)

    bow_values = [b3_per_seed[s]["bow_stand_in_recall_at_1"] for s in seeds]
    bow_stats = _compute_statistics(bow_values)

    metadata_values = [b3_per_seed[s]["metadata_filter_recall_at_1"] for s in seeds]
    metadata_stats = _compute_statistics(metadata_values)

    hardware = detect_hardware_profile()
    dataset_sha = _dataset_sha256(config.lengths, seeds)

    real_embedding_first = b3_per_seed[seeds[0]].get("real_embedding", {})
    real_embedding_available = real_embedding_first.get("available", False)

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
                name=DATASET_NAME,
                version="v1",
                split="benchmark",
                record_count=len(seeds),
            )
        ],
        metrics={
            "retrieval": MetricGroup(
                status="present",
                metrics={
                    "qp_recall_at_1": MetricEntry(
                        value=qp_recall_stats.mean,
                        unit="ratio",
                        description="Qp needle recall at rank 1 across pinned seeds.",
                        statistics=qp_recall_stats,
                    ),
                    "qp_recall_at_k": MetricEntry(
                        value=float(statistics.mean(per_seed[s]["qp_recall_at_k"] for s in seeds)),
                        unit="ratio",
                        description=f"Qp needle recall within top {config.top_k} across pinned seeds.",
                    ),
                    "vector_recall_at_1": MetricEntry(
                        value=vector_recall_stats.mean,
                        unit="ratio",
                        description="Vector-RAG needle recall at rank 1 across pinned seeds.",
                        statistics=vector_recall_stats,
                    ),
                    "vector_recall_at_k": MetricEntry(
                        value=float(statistics.mean(per_seed[s]["vector_recall_at_k"] for s in seeds)),
                        unit="ratio",
                        description=f"Vector-RAG needle recall within top {config.top_k} across pinned seeds.",
                    ),
                    "per_seed_qp_recall_at_1": MetricEntry(
                        value=json.dumps({str(s): per_seed[s]["qp_recall_at_1"] for s in seeds}),
                        unit="json",
                        description="Per-seed Qp recall@1 values.",
                    ),
                    "per_seed_vector_recall_at_1": MetricEntry(
                        value=json.dumps({str(s): per_seed[s]["vector_recall_at_1"] for s in seeds}),
                        unit="json",
                        description="Per-seed vector recall@1 values.",
                    ),
                    "bow_stand_in_recall_at_1": MetricEntry(
                        value=bow_stats.mean,
                        unit="ratio",
                        description="B3 BoW stand-in needle recall@1 across pinned seeds.",
                        statistics=bow_stats,
                    ),
                    "metadata_filter_recall_at_1": MetricEntry(
                        value=metadata_stats.mean,
                        unit="ratio",
                        description="B3 metadata-filter needle recall@1 across pinned seeds (same structural metadata as DSS).",
                        statistics=metadata_stats,
                    ),
                    "real_embedding_recall_at_1": MetricEntry(
                        value=real_embedding_first.get("recall_at_1", -1.0) if real_embedding_available else -1.0,
                        unit="ratio",
                        description=f"B3 real embedding baseline ({PINNED_MODEL_NAME}) needle recall@1 on first seed.",
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
                        value=len(seeds),
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
            "credit": "hugooconnor — issue #1 reproduction and critique",
            "partial_status_note": "'partial' indicates traceability/governance groups are intentionally out-of-scope, not retrieval failure.",
            "b3_real_embedding_available": real_embedding_available,
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

    per_seed: dict[int, dict[str, float]] = {}
    b3_per_seed: dict[int, dict[str, Any]] = {}

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
        memories, queries = generate_corpus(cfg.lengths, seed=seed)
        summary, _ = evaluate(
            memories,
            queries,
            top_k=cfg.top_k,
            permutations=cfg.permutations,
            seed=seed,
        )
        per_seed[seed] = {
            "qp_recall_at_1": summary.qp_recall_at_1,
            "qp_recall_at_k": summary.qp_recall_at_k,
            "vector_recall_at_1": summary.vector_recall_at_1,
            "vector_recall_at_k": summary.vector_recall_at_k,
        }
        b3_per_seed[seed] = _run_b3_baselines(memories, queries, cfg.top_k)
        real_emb = b3_per_seed[seed].get("real_embedding", {})
        real_note = f" real@1={real_emb.get('recall_at_1'):.3f}" if real_emb.get("available") else " real=unavailable"
        print(
            f"seed {seed}: qp@1={summary.qp_recall_at_1:.3f} "
            f"vector@1={summary.vector_recall_at_1:.3f} "
            f"bow@1={b3_per_seed[seed]['bow_stand_in_recall_at_1']:.3f} "
            f"meta@1={b3_per_seed[seed]['metadata_filter_recall_at_1']:.3f}"
            f"{real_note}"
        )

    runtime_ms = (time.perf_counter() - start) * 1000.0

    artifact = _build_distribution_artifact(
        seeds=args.seeds,
        per_seed=per_seed,
        b3_per_seed=b3_per_seed,
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

    dataset_sha = _dataset_sha256(config.lengths, args.seeds)
    manifest = build_manifest(
        artifact,
        eval_script_version="seed_distribution_harness_v1.1",
        seeds=args.seeds,
        conditions={
            "lengths": ",".join(str(x) for x in config.lengths),
            "top_k": config.top_k,
            "permutations": config.permutations,
            "transport": "R1",
            "issue": "#1",
        },
        dataset_path=f"generator:{DATASET_NAME}:v1",
        dataset_sha256=dataset_sha,
    )
    manifest_path = output_path.with_suffix(".manifest.json")
    write_manifest(manifest, manifest_path)

    print(f"\nSeed distribution artifact: {output_path}")
    print(f"Manifest: {manifest_path}")
    print(f"Mean qp_recall@1: {artifact.metrics['retrieval'].metrics['qp_recall_at_1'].statistics.mean:.3f}")
    print(f"Mean vector_recall@1: {artifact.metrics['retrieval'].metrics['vector_recall_at_1'].statistics.mean:.3f}")
    print(f"n={len(args.seeds)} | issue #1 | credit: hugooconnor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
