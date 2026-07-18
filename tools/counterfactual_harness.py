#!/usr/bin/env python3
"""
KSR-EVAL DSS-277 — Counterfactual shuffles and matched-information baselines.

Runs counterfactual arms on the deterministic needle and multi-hop harnesses:
  A: original (text + coordinate paired as designed)
  B: shuffle texts, hold coordinates  — tests whether retrieval is text-driven
  C: shuffle coordinates, hold texts  — tests whether retrieval is coordinate-driven

Also reports the B3 matched-information baselines:
  - bow_stand_in  (renamed from DenseRetrievalBaseline)
  - real_embedding (all-MiniLM-L6-v2, local CPU, pinned weights)
  - metadata_filter (structural metadata only)

Output is a BenchmarkArtifact per whitepaper Appendix A plus a human-readable
report under ``eval/reports/benchmarks/``. Hugo is credited as co-author of the
original synthetic corpora. Decision D2 merge window is documented in the
artifact run_config.

Usage:
    PYTHONPATH=apps/backend python3 tools/counterfactual_harness.py
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import random
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
from backend.benchmarks.comparison_benchmark import (  # noqa: E402
    run_multihop_baseline,
    run_needle_baseline,
)
from backend.benchmarks.hardware import detect_hardware_profile  # noqa: E402
from backend.benchmarks.longbench_multihop_benchmark import (  # noqa: E402
    DEFAULT_CHAIN_COUNT,
    DEFAULT_CHAIN_LENGTH,
    DEFAULT_TOP_K as MULTIHOP_TOP_K,
    evaluate as evaluate_multihop,
    generate_corpus as generate_multihop_corpus,
)
from backend.benchmarks.longbench_needle_benchmark import (  # noqa: E402
    DEFAULT_LENGTHS,
    DEFAULT_TOP_K as NEEDLE_TOP_K,
    evaluate as evaluate_needle,
    generate_corpus as generate_needle_corpus,
)
from backend.benchmarks.metadata_filter_baseline import (  # noqa: E402
    CoordinateMetadata,
    MetadataFilterBaseline,
)
from backend.benchmarks.real_embedding_baseline import (  # noqa: E402
    PINNED_MODEL_NAME,
    RealEmbeddingBaseline,
)


DEFAULT_SEED = 2026
DEFAULT_OUTPUT_ROOT = Path("eval/reports/benchmarks")
D2_WINDOW_DAYS = 14


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


def _shuffle_texts_hold_coords(memories: list[Any], rng: random.Random) -> list[Any]:
    """Return memories with texts shuffled and coordinates preserved."""
    import dataclasses

    texts = [m.text for m in memories]
    rng.shuffle(texts)
    return [
        dataclasses.replace(mem, text=text) for mem, text in zip(memories, texts)
    ]


def _shuffle_coords_hold_texts(memories: list[Any], rng: random.Random) -> list[Any]:
    """Return memories with coordinates shuffled and texts preserved."""
    import dataclasses

    coords = [m.coordinate for m in memories]
    rng.shuffle(coords)
    return [
        dataclasses.replace(mem, coordinate=coord) for mem, coord in zip(memories, coords)
    ]


def _extract_coordinate_metadata(coord: Any) -> dict[str, Any]:
    """Extract structural metadata from a QpCoordinate for the metadata filter baseline."""
    return {
        "kernel_node": getattr(coord, "kernel_node", None),
        "valuation_offset": getattr(coord, "valuation_offset", None),
        "circulation_pass": getattr(coord, "circulation_pass", None),
        "tetrahedron": getattr(coord, "tetrahedron", None),
        "dual_valid": getattr(coord, "dual_state", None) is not None,
    }


def _normalize_memories_with_metadata(memories: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(getattr(mem, "memory_id", i)),
            "text": str(getattr(mem, "text", "")),
            "coordinate_metadata": _extract_coordinate_metadata(getattr(mem, "coordinate", None)),
        }
        for i, mem in enumerate(memories)
    ]


def _normalize_needle_queries(queries: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(q.query_id),
            "text": str(q.text),
            "relevant_ids": {str(q.needle_id)},
            "coordinate_metadata": _extract_coordinate_metadata(q.coordinate),
        }
        for q in queries
    ]


def _normalize_multihop_queries(queries: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(q.query_id),
            "text": str(q.text),
            "relevant_ids": set(q.required_ids),
            "coordinate_metadata": _extract_coordinate_metadata(q.coordinate),
        }
        for q in queries
    ]


def run_counterfactual_needle(
    *,
    seed: int,
    lengths: tuple[int, ...] = DEFAULT_LENGTHS,
    top_k: int = NEEDLE_TOP_K,
) -> dict[str, Any]:
    rng = random.Random(seed)
    memories, queries = generate_needle_corpus(lengths, seed=seed)

    arms: dict[str, list[Any]] = {
        "A_original": memories,
        "B_shuffle_texts": _shuffle_texts_hold_coords(memories, rng),
        "C_shuffle_coords": _shuffle_coords_hold_texts(memories, rng),
    }

    results = {}
    for arm_name, arm_memories in arms.items():
        summary, _ = evaluate_needle(
            arm_memories,
            queries,
            top_k=top_k,
            permutations=5_000,
            seed=seed,
        )
        results[arm_name] = {
            "qp_recall_at_1": summary.qp_recall_at_1,
            "vector_recall_at_1": summary.vector_recall_at_1,
            "qp_recall_at_k": summary.qp_recall_at_k,
            "vector_recall_at_k": summary.vector_recall_at_k,
        }

    return {
        "benchmark": "longbench-needle",
        "seed": seed,
        "lengths": lengths,
        "top_k": top_k,
        "arms": results,
        "interpretation": (
            "coordinate_driven"
            if results["C_shuffle_coords"]["qp_recall_at_1"] < results["A_original"]["qp_recall_at_1"]
            and results["B_shuffle_texts"]["qp_recall_at_1"] >= results["A_original"]["qp_recall_at_1"]
            else "mixed_or_text_driven"
        ),
    }


def run_counterfactual_multihop(
    *,
    seed: int,
    chain_count: int = DEFAULT_CHAIN_COUNT,
    chain_length: int = DEFAULT_CHAIN_LENGTH,
    top_k: int = MULTIHOP_TOP_K,
) -> dict[str, Any]:
    rng = random.Random(seed)
    memories, queries = generate_multihop_corpus(chain_count, seed=seed)

    arms: dict[str, list[Any]] = {
        "A_original": memories,
        "B_shuffle_texts": _shuffle_texts_hold_coords(memories, rng),
        "C_shuffle_coords": _shuffle_coords_hold_texts(memories, rng),
    }

    results = {}
    for arm_name, arm_memories in arms.items():
        summary, _ = evaluate_multihop(
            arm_memories,
            queries,
            top_k=top_k,
            permutations=5_000,
            seed=seed,
        )
        results[arm_name] = {
            "qp_chain_recall": summary.qp_chain_recall,
            "vector_chain_recall": summary.vector_chain_recall,
            "qp_full_chain_rate": summary.qp_full_chain_rate,
            "vector_full_chain_rate": summary.vector_full_chain_rate,
        }

    return {
        "benchmark": "longbench-multihop",
        "seed": seed,
        "chain_count": chain_count,
        "chain_length": chain_length,
        "top_k": top_k,
        "arms": results,
        "interpretation": (
            "coordinate_driven"
            if results["C_shuffle_coords"]["qp_chain_recall"] < results["A_original"]["qp_chain_recall"]
            and results["B_shuffle_texts"]["qp_chain_recall"] >= results["A_original"]["qp_chain_recall"]
            else "mixed_or_text_driven"
        ),
    }


def run_b3_baselines(
    *,
    seed: int,
    needle_lengths: tuple[int, ...] = (8, 16, 32),
    chain_count: int = 5,
) -> dict[str, Any]:
    """Run B3 matched-information baselines on small needle + multi-hop splits."""
    bow = BASELINES["bow_stand_in"]
    metadata = MetadataFilterBaseline()

    # Needle baselines.
    memories, queries = generate_needle_corpus(needle_lengths, seed=seed)
    norm_memories = _normalize_memories_with_metadata(memories)
    norm_queries = _normalize_needle_queries(queries)

    bow_needle = bow.run(norm_memories, norm_queries, top_k=NEEDLE_TOP_K)
    metadata_needle = metadata.run(norm_memories, norm_queries, top_k=NEEDLE_TOP_K)

    # Multi-hop baselines.
    mh_memories, mh_queries = generate_multihop_corpus(chain_count, seed=seed)
    mh_norm_memories = _normalize_memories_with_metadata(mh_memories)
    mh_norm_queries = _normalize_multihop_queries(mh_queries)

    bow_multihop = bow.run(mh_norm_memories, mh_norm_queries, top_k=MULTIHOP_TOP_K)
    metadata_multihop = metadata.run(mh_norm_memories, mh_norm_queries, top_k=MULTIHOP_TOP_K)

    real_embedding_result = {"available": False, "reason": "sentence-transformers not available or skipped"}
    try:
        real_emb = RealEmbeddingBaseline()
        real_needle = real_emb.run(norm_memories, norm_queries, top_k=NEEDLE_TOP_K)
        real_multihop = real_emb.run(mh_norm_memories, mh_norm_queries, top_k=MULTIHOP_TOP_K)
        real_embedding_result = {
            "available": True,
            "model": PINNED_MODEL_NAME,
            "weights_sha256": real_emb.model_info.weights_sha256 if real_emb.model_info else None,
            "needle_recall_at_1": real_needle.recall_at_1,
            "multihop_recall_at_k": real_multihop.recall_at_k,
        }
    except Exception as exc:
        real_embedding_result["reason"] = str(exc)

    return {
        "needle": {
            "bow_stand_in_recall_at_1": bow_needle.recall_at_1,
            "metadata_filter_recall_at_1": metadata_needle.recall_at_1,
            "real_embedding": real_embedding_result.get("needle_recall_at_1"),
        },
        "multihop": {
            "bow_stand_in_recall_at_k": bow_multihop.recall_at_k,
            "metadata_filter_recall_at_k": metadata_multihop.recall_at_k,
            "real_embedding": real_embedding_result.get("multihop_recall_at_k"),
        },
        "real_embedding": real_embedding_result,
    }


def _build_artifact(
    *,
    needle_cf: dict[str, Any],
    multihop_cf: dict[str, Any],
    b3: dict[str, Any],
    repo_sha: str,
    executed_at: datetime,
    runtime_ms: float,
    seed: int,
) -> BenchmarkArtifact:
    hardware = detect_hardware_profile()
    return BenchmarkArtifact(
        artefact_schema_version="1.0.0",
        run_id=f"counterfactual-baselines-{executed_at.strftime('%Y%m%dT%H%M%SZ')}",
        suite_id="counterfactual-baselines",
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
                record_count=1,
            ),
            DatasetRef(
                name="longbench_multihop_synthetic_v1",
                version="v1",
                split="benchmark",
                record_count=1,
            ),
        ],
        metrics={
            "retrieval": MetricGroup(
                status="present",
                metrics={
                    "needle_original_qp_recall_at_1": MetricEntry(
                        value=needle_cf["arms"]["A_original"]["qp_recall_at_1"],
                        unit="ratio",
                        description="Needle Qp recall@1 on original paired corpus.",
                    ),
                    "needle_shuffle_texts_qp_recall_at_1": MetricEntry(
                        value=needle_cf["arms"]["B_shuffle_texts"]["qp_recall_at_1"],
                        unit="ratio",
                        description="Needle Qp recall@1 when texts are shuffled and coordinates held.",
                    ),
                    "needle_shuffle_coords_qp_recall_at_1": MetricEntry(
                        value=needle_cf["arms"]["C_shuffle_coords"]["qp_recall_at_1"],
                        unit="ratio",
                        description="Needle Qp recall@1 when coordinates are shuffled and texts held.",
                    ),
                    "multihop_original_qp_chain_recall": MetricEntry(
                        value=multihop_cf["arms"]["A_original"]["qp_chain_recall"],
                        unit="ratio",
                        description="Multi-hop Qp chain recall on original paired corpus.",
                    ),
                    "multihop_shuffle_texts_qp_chain_recall": MetricEntry(
                        value=multihop_cf["arms"]["B_shuffle_texts"]["qp_chain_recall"],
                        unit="ratio",
                        description="Multi-hop Qp chain recall when texts are shuffled and coordinates held.",
                    ),
                    "multihop_shuffle_coords_qp_chain_recall": MetricEntry(
                        value=multihop_cf["arms"]["C_shuffle_coords"]["qp_chain_recall"],
                        unit="ratio",
                        description="Multi-hop Qp chain recall when coordinates are shuffled and texts held.",
                    ),
                    "b3_bow_stand_in_needle_recall_at_1": MetricEntry(
                        value=b3["needle"]["bow_stand_in_recall_at_1"],
                        unit="ratio",
                        description="B3 BoW stand-in needle recall@1.",
                    ),
                    "b3_metadata_filter_needle_recall_at_1": MetricEntry(
                        value=b3["needle"]["metadata_filter_recall_at_1"],
                        unit="ratio",
                        description="B3 metadata-filter needle recall@1.",
                    ),
                },
            ),
            "latency": MetricGroup(
                status="present",
                metrics={
                    "total_runtime_ms": MetricEntry(
                        value=runtime_ms,
                        unit="ms",
                        description="Total harness runtime.",
                    )
                },
            ),
            "cost": MetricGroup(
                status="present",
                metrics={
                    "embedding_queries": MetricEntry(
                        value=1,
                        unit="count",
                        description="Counterfactual run marker (no LLM tokens consumed).",
                    )
                },
            ),
            "traceability": MetricGroup(
                status="absent",
                absence_reason="Traceability metrics are out of scope for counterfactual shuffle tests.",
            ),
            "governance": MetricGroup(
                status="absent",
                absence_reason="Governance metrics are out of scope for counterfactual shuffle tests.",
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
            "seed": seed,
            "issue": "#1",
            "credit": "Hugo Berigny — original synthetic corpus design",
            "d2_merge_window_days": D2_WINDOW_DAYS,
            "d2_note": (
                f"Hold merge of counterfactual shuffle distributions until Hugo's "
                f"stated PR window expires ({D2_WINDOW_DAYS} days) or his PR lands."
            ),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="KSR-EVAL DSS-277 counterfactual harness")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--lengths",
        type=lambda s: tuple(int(x.strip()) for x in s.split(",")),
        default=(8, 16, 32),
        help="Comma-separated haystack lengths for needle arm.",
    )
    parser.add_argument(
        "--chain-count",
        type=int,
        default=5,
        help="Number of chains for multi-hop arm.",
    )
    args = parser.parse_args()

    repo_sha = _repo_sha()
    executed_at = datetime.now(timezone.utc)
    start = time.perf_counter()

    needle_cf = run_counterfactual_needle(seed=args.seed, lengths=args.lengths)
    multihop_cf = run_counterfactual_multihop(seed=args.seed, chain_count=args.chain_count)
    b3 = run_b3_baselines(seed=args.seed, needle_lengths=args.lengths, chain_count=args.chain_count)

    runtime_ms = (time.perf_counter() - start) * 1000.0

    artifact = _build_artifact(
        needle_cf=needle_cf,
        multihop_cf=multihop_cf,
        b3=b3,
        repo_sha=repo_sha,
        executed_at=executed_at,
        runtime_ms=runtime_ms,
        seed=args.seed,
    )

    args.output_root.mkdir(parents=True, exist_ok=True)
    output_path = args.output_root / f"counterfactual_baselines_{executed_at.strftime('%Y%m%dT%H%M%SZ')}_{repo_sha[:12]}.json"
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )

    # Emit KSR-EVAL v0.4 manifest.
    from backend.benchmarks.manifest import build_manifest, write_manifest

    manifest = build_manifest(
        artifact,
        eval_script_version="counterfactual_harness_v1.0",
        seeds=[args.seed],
        conditions={
            "lengths": ",".join(str(x) for x in args.lengths),
            "chain_count": args.chain_count,
            "top_k": NEEDLE_TOP_K,
            "transport": "R1",
            "issue": "#1",
        },
    )
    manifest_path = output_path.with_suffix(".manifest.json")
    write_manifest(manifest, manifest_path)

    print("Counterfactual shuffle + baseline harness (DSS-277)")
    print("=" * 60)
    print(f"Needle interpretation: {needle_cf['interpretation']}")
    for arm, metrics in needle_cf["arms"].items():
        print(f"  {arm}: qp@1={metrics['qp_recall_at_1']:.3f} vector@1={metrics['vector_recall_at_1']:.3f}")
    print(f"\nMulti-hop interpretation: {multihop_cf['interpretation']}")
    for arm, metrics in multihop_cf["arms"].items():
        print(f"  {arm}: qp_chain_recall={metrics['qp_chain_recall']:.3f} vector_chain_recall={metrics['vector_chain_recall']:.3f}")
    print("\nB3 matched-information baselines:")
    print(f"  BoW stand-in needle@1: {b3['needle']['bow_stand_in_recall_at_1']:.3f}")
    print(f"  Metadata filter needle@1: {b3['needle']['metadata_filter_recall_at_1']:.3f}")
    print(f"  Real embedding available: {b3['real_embedding']['available']}")
    print(f"\nArtifact: {output_path}")
    print(f"Manifest: {manifest_path}")
    print(f"D2 window: {D2_WINDOW_DAYS} days | issue #1 | credit: Hugo Berigny")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
