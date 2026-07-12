"""Broader-comparison benchmark harness for DSS-227.

Runs external baseline adapters against the existing LongBench needle and
multi-hop corpora, producing validated ``BenchmarkArtifact`` records that can
be aggregated by the multi-seed harness.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.benchmarks.artifact_schema import (
    BenchmarkArtifact,
    BenchmarkMode,
    HardwareProfile as SchemaHardwareProfile,
)
from backend.benchmarks.comparison_baselines import (
    BASELINES,
    Baseline,
    BaselineResult,
    GrokBaseline,
)
from backend.benchmarks.hardware import detect_hardware_profile
from backend.benchmarks.longbench_multihop_benchmark import (
    DEFAULT_CHAIN_COUNT,
    generate_corpus as generate_multihop_corpus,
)
from backend.benchmarks.longbench_needle_benchmark import (
    DEFAULT_LENGTHS,
    generate_corpus as generate_needle_corpus,
)


BenchmarkName = str


def _repo_sha() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent.parent.parent)
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _mode_for_baseline(baseline: Baseline) -> BenchmarkMode:
    mapping: dict[str, BenchmarkMode] = {
        "dense_retrieval": "baseline_dense",
        "hierarchical_rag": "baseline_hierarchical",
        "long_context_model": "baseline_long_context",
        "grok_latest": "baseline_grok",
    }
    return mapping.get(baseline.name, "baseline_dense")


def _normalize_memories(
    memories: Sequence[Any],
) -> list[dict[str, Any]]:
    return [
        {
            "id": str(getattr(mem, "memory_id", "")),
            "text": str(getattr(mem, "text", "")),
        }
        for mem in memories
    ]


def _normalize_needle_queries(queries: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(getattr(q, "query_id", "")),
            "text": str(getattr(q, "text", "")),
            "relevant_ids": {str(getattr(q, "needle_id", ""))},
        }
        for q in queries
    ]


def _normalize_multihop_queries(queries: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(getattr(q, "query_id", "")),
            "text": str(getattr(q, "text", "")),
            "relevant_ids": set(getattr(q, "required_ids", [])),
        }
        for q in queries
    ]


def _build_artifact(
    baseline: Baseline,
    result: BaselineResult,
    *,
    benchmark_name: str,
    benchmark_version: str,
    executed_at: datetime,
    seed: int,
    record_count: int,
    extra_run_config: Mapping[str, Any] | None = None,
) -> BenchmarkArtifact:
    hardware_profile = detect_hardware_profile()
    hardware = SchemaHardwareProfile(**hardware_profile.to_dict())
    blocked = isinstance(baseline, GrokBaseline)
    run_config: dict[str, Any] = {
        "baseline": baseline.name,
        "benchmark": benchmark_name,
        "seed": seed,
        "blocked": blocked,
    }
    if blocked:
        run_config["blocked_reason"] = GrokBaseline.blocked_reason
    if extra_run_config:
        run_config.update(extra_run_config)

    return BenchmarkArtifact(
        artefact_schema_version="1.0.0",
        run_id=f"comparison-{benchmark_name}-{baseline.name}-{executed_at.strftime('%Y%m%dT%H%M%SZ')}-{seed}",
        suite_id=f"comparison-{benchmark_name}",
        suite_version=benchmark_version,
        executed_at=executed_at,
        mode=_mode_for_baseline(baseline),
        status="partial",
        repos=[
            {
                "name": "ds-backend-local",
                "commit_sha": _repo_sha(),
                "role": "canonical_benchmark_engine",
                "required_for_run": True,
            }
        ],
        datasets=[
            {
                "name": benchmark_name,
                "version": benchmark_version,
                "split": "benchmark",
                "record_count": record_count,
            }
        ],
        metrics={
            "retrieval": {
                "status": "present",
                "metrics": {
                    "recall_at_1": {
                        "value": result.recall_at_1,
                        "unit": "ratio",
                        "description": "Fraction of queries with a relevant memory at rank 1.",
                    },
                    "recall_at_k": {
                        "value": result.recall_at_k,
                        "unit": "ratio",
                        "description": "Fraction of queries with a relevant memory in the top k.",
                    },
                    "mrr": {
                        "value": result.mrr,
                        "unit": "ratio",
                        "description": "Mean reciprocal rank.",
                    },
                },
            },
            "latency": {
                "status": "present",
                "metrics": {
                    "avg_latency_ms": {
                        "value": result.avg_latency_ms,
                        "unit": "ms",
                        "description": "Average end-to-end latency per query.",
                    },
                },
            },
            "cost": {
                "status": "present",
                "metrics": {
                    "token_cost": {
                        "value": result.token_cost,
                        "unit": "tokens",
                        "description": "Estimated total token cost per query.",
                    },
                    "prompt_tokens": {
                        "value": result.prompt_tokens,
                        "unit": "tokens",
                        "description": "Prompt tokens per query.",
                    },
                    "completion_tokens": {
                        "value": result.completion_tokens,
                        "unit": "tokens",
                        "description": "Completion tokens per query.",
                    },
                },
            },
            "traceability": {
                "status": "absent",
                "absence_reason": "comparison_runner_does_not_measure_traceability_yet",
            },
            "governance": {
                "status": "absent",
                "absence_reason": "comparison_runner_does_not_measure_governance_yet",
            },
        },
        freshness={
            "status": "fresh",
            "checked_at": executed_at,
            "max_age_hours": 24,
            "age_hours": 0.0,
        },
        hardware=hardware,
        run_config=run_config,
    )


def run_needle_baseline(
    baseline: Baseline,
    *,
    seed: int = 193,
    top_k: int = 10,
    lengths: Sequence[int] = DEFAULT_LENGTHS,
) -> BenchmarkArtifact:
    """Run a baseline on the LongBench needle-in-a-haystack corpus."""
    memories, queries = generate_needle_corpus(lengths, seed=seed)
    result = baseline.run(
        _normalize_memories(memories),
        _normalize_needle_queries(queries),
        top_k=top_k,
    )
    return _build_artifact(
        baseline,
        result,
        benchmark_name="longbench-needle",
        benchmark_version="v1",
        executed_at=datetime.now(timezone.utc),
        seed=seed,
        record_count=len(queries),
        extra_run_config={"lengths": ",".join(str(x) for x in lengths), "top_k": top_k},
    )


def run_multihop_baseline(
    baseline: Baseline,
    *,
    seed: int = 193,
    top_k: int = 5,
    chain_count: int = DEFAULT_CHAIN_COUNT,
) -> BenchmarkArtifact:
    """Run a baseline on the LongBench multi-hop corpus."""
    memories, queries = generate_multihop_corpus(chain_count, seed=seed)
    result = baseline.run(
        _normalize_memories(memories),
        _normalize_multihop_queries(queries),
        top_k=top_k,
    )
    return _build_artifact(
        baseline,
        result,
        benchmark_name="longbench-multihop",
        benchmark_version="v1",
        executed_at=datetime.now(timezone.utc),
        seed=seed,
        record_count=len(queries),
        extra_run_config={"chain_count": chain_count, "top_k": top_k},
    )


def _normalize_ruler_queries(queries: Sequence[Any]) -> list[dict[str, Any]]:
    """Normalise RULER needle queries to the comparison interface."""
    return [
        {
            "id": str(getattr(q, "query_id", "")),
            "text": str(getattr(q, "text", "")),
            "relevant_ids": {str(getattr(q, "needle_id", ""))},
        }
        for q in queries
    ]


def run_ruler_baseline(
    baseline: Baseline,
    *,
    seed: int = 193,
    top_k: int = 10,
    haystack_length: int = 1000,
) -> BenchmarkArtifact:
    """Run a baseline on a synthetic RULER-style long-context needle corpus."""
    memories, queries = generate_needle_corpus([haystack_length], seed=seed)
    result = baseline.run(
        _normalize_memories(memories),
        _normalize_ruler_queries(queries),
        top_k=top_k,
    )
    return _build_artifact(
        baseline,
        result,
        benchmark_name="ruler-256k",
        benchmark_version="v1",
        executed_at=datetime.now(timezone.utc),
        seed=seed,
        record_count=len(queries),
        extra_run_config={"haystack_length": haystack_length, "top_k": top_k},
    )


__all__ = (
    "run_needle_baseline",
    "run_multihop_baseline",
    "run_ruler_baseline",
    "BASELINES",
)
