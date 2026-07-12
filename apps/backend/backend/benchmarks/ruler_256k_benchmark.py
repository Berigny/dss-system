"""Minimal synthetic RULER 256K benchmark runner.

This harness reuses the LongBench needle corpus generator and Qp routing from
``longbench_needle_benchmark.py``. It creates a single ~256K-token haystack
(13,000 short memories) with one needle, then evaluates retrieval in three
modes:

- semantic_only: deterministic bag-of-words cosine (vector-RAG baseline)
- coordinate_guided: Qp coordinate routing with architecture filters
- full_dss: blended coordinate + semantic score

No LLM calls are made. The output is a set of valid BenchmarkArtifact JSON
files suitable for ``publish_dashboard_benchmarks.py``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np

from backend.benchmarks.artifact_schema import BenchmarkArtifact
from backend.benchmarks.longbench_needle_benchmark import (
    DEFAULT_SEED,
    NeedleMemory,
    NeedleQuery,
    QpRouter,
    VectorRAGBaseline,
    generate_corpus,
    qp_pure_compatible,
    qp_score,
)
from backend.fieldx_kernel.qp_coordinate import qp_coordinate_distance
from backend.search.token_index import normalise_tokens


DEFAULT_OUTPUT_ROOT = Path(__file__).parent / "output" / "RULER256K"
HAYSTACK_LENGTH = 13_000
TOP_K = 10
FULL_DSS_SEMANTIC_WEIGHT = 0.3
FULL_DSS_COORDINATE_WEIGHT = 0.7


@dataclass(frozen=True)
class ModeResult:
    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    avg_latency_ms: float
    token_cost: float
    queries: int
    needle_rank: int | None


def _repo_sha() -> str:
    git_sha = os.getenv("GIT_SHA", "")
    if git_sha:
        return git_sha
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent.parent.parent)
            .decode()
            .strip()[:7]
        )
    except Exception:  # pragma: no cover
        return "unknown"


def _recall_at_k(rank: int | None, k: int) -> float:
    return 1.0 if rank is not None and rank < k else 0.0


def _mrr(rank: int | None) -> float:
    return 1.0 / (rank + 1) if rank is not None else 0.0


def _total_corpus_tokens(memories: Sequence[NeedleMemory]) -> int:
    return sum(len(normalise_tokens(m.text)) for m in memories)


def _evaluate_mode(
    memories: Sequence[NeedleMemory],
    queries: Sequence[NeedleQuery],
    *,
    mode: str,
    top_k: int,
) -> ModeResult:
    assert mode in {"semantic_only", "coordinate_guided", "full_dss"}

    vector_baseline = VectorRAGBaseline(memories)
    qp_router = QpRouter(memories)

    latencies: list[float] = []
    ranks: list[int | None] = []

    for query in queries:
        start = time.perf_counter()

        if mode == "semantic_only":
            ranked = [mid for mid, _ in vector_baseline.rank(query.text, top_k=max(top_k, 10))]
        elif mode == "coordinate_guided":
            ranked = [mid for mid, _ in qp_router.rank(query, top_k=max(top_k, 10))]
        else:  # full_dss
            # Build full vector score table.
            vector_scores = {
                mid: score for mid, score in vector_baseline.rank(query.text, top_k=len(memories))
            }
            # Build full Qp score table (incompatible candidates get 0).
            qp_scores = {
                mid: score for mid, score in qp_router.rank(query, top_k=len(memories))
            }
            blended: list[tuple[float, str]] = []
            for memory in memories:
                vs = vector_scores.get(memory.memory_id, 0.0)
                qs = qp_scores.get(memory.memory_id, 0.0)
                score = FULL_DSS_SEMANTIC_WEIGHT * vs + FULL_DSS_COORDINATE_WEIGHT * qs
                blended.append((score, memory.memory_id))
            blended.sort(key=lambda pair: pair[0], reverse=True)
            ranked = [mid for _, mid in blended[: max(top_k, 10)]]

        latencies.append((time.perf_counter() - start) * 1000.0)
        rank = ranked.index(query.needle_id) if query.needle_id in ranked else None
        ranks.append(rank)

    query_count = len(queries)
    avg_latency = sum(latencies) / query_count if query_count else 0.0
    total_tokens = _total_corpus_tokens(memories)
    token_cost = float(total_tokens + query_count * len(normalise_tokens(queries[0].text)) * 4 + top_k * 48)

    return ModeResult(
        recall_at_1=_recall_at_k(ranks[0], 1) if ranks else 0.0,
        recall_at_5=_recall_at_k(ranks[0], 5) if ranks else 0.0,
        recall_at_10=_recall_at_k(ranks[0], 10) if ranks else 0.0,
        mrr=_mrr(ranks[0]) if ranks else 0.0,
        avg_latency_ms=avg_latency,
        token_cost=token_cost,
        queries=query_count,
        needle_rank=ranks[0] if ranks else None,
    )


def _build_artifact(
    result: ModeResult,
    *,
    mode: str,
    executed_at: datetime,
    repo_sha: str,
    haystack_length: int,
    total_tokens: int,
    artefact_schema_version: str = "1.0.0",
) -> BenchmarkArtifact:
    run_suffix = executed_at.strftime("%Y%m%dT%H%M%SZ")
    return BenchmarkArtifact(
        artefact_schema_version=artefact_schema_version,
        run_id=f"RULER256K-{mode}-{run_suffix}",
        suite_id="RULER256K",
        suite_version="phase1-v1",
        executed_at=executed_at,
        mode=mode,  # type: ignore[arg-type]
        status="partial",
        repos=[
            {
                "name": "ds-backend-local",
                "commit_sha": repo_sha,
                "role": "canonical_benchmark_engine",
                "required_for_run": True,
            }
        ],
        datasets=[
            {
                "name": "ruler_256k_synthetic",
                "version": "phase1-v1",
                "split": "benchmark",
                "record_count": haystack_length + 1,
            }
        ],
        metrics={
            "retrieval": {
                "status": "present",
                "metrics": {
                    "recall_at_10": {"value": result.recall_at_10, "unit": "ratio"},
                    "recall_at_5": {"value": result.recall_at_5, "unit": "ratio"},
                    "recall_at_1": {"value": result.recall_at_1, "unit": "ratio"},
                    "mrr": {"value": result.mrr, "unit": "ratio"},
                    "queries": {"value": result.queries, "unit": "count"},
                },
            },
            "traceability": {
                "status": "absent",
                "absence_reason": "ruler_256k_runner_does_not_measure_traceability_yet",
            },
            "governance": {
                "status": "absent",
                "absence_reason": "ruler_256k_runner_does_not_measure_governance_yet",
            },
            "latency": {
                "status": "present",
                "metrics": {
                    "avg_latency_ms": {"value": result.avg_latency_ms, "unit": "ms"},
                },
            },
            "cost": {
                "status": "present",
                "metrics": {
                    "token_cost": {"value": result.token_cost, "unit": "tokens"},
                },
            },
        },
        freshness={
            "status": "fresh",
            "checked_at": executed_at,
            "max_age_hours": 24,
            "age_hours": 0,
        },
        run_config={
            "mode_label": {
                "semantic_only": "Deterministic lexical retrieval baseline.",
                "coordinate_guided": "Prime-factor coordinate retrieval baseline.",
                "full_dss": "Blended lexical plus coordinate-guided retrieval baseline.",
            }[mode],
            "top_k": TOP_K,
            "haystack_length": haystack_length,
            "total_tokens": total_tokens,
        },
    )


def run_benchmark(
    *,
    output_root: Path,
    haystack_length: int = HAYSTACK_LENGTH,
    top_k: int = TOP_K,
    seed: int = DEFAULT_SEED,
    artefact_schema_version: str = "1.0.0",
) -> dict[str, ModeResult]:
    output_root.mkdir(parents=True, exist_ok=True)
    repo_sha = _repo_sha()

    print(f"Generating RULER 256K corpus with haystack length {haystack_length}...")
    memories, queries = generate_corpus(lengths=[haystack_length], seed=seed)
    total_tokens = _total_corpus_tokens(memories)
    print(f"  {len(memories):,} memories, ~{total_tokens:,} tokens")

    results: dict[str, ModeResult] = {}
    for mode in ("semantic_only", "coordinate_guided", "full_dss"):
        print(f"Running {mode}...")
        executed_at = datetime.now(timezone.utc)
        start = time.perf_counter()
        result = _evaluate_mode(memories, queries, mode=mode, top_k=top_k)
        eval_time = time.perf_counter() - start
        results[mode] = result
        print(
            f"  recall@1={result.recall_at_1:.2f} recall@10={result.recall_at_10:.2f} "
            f"mrr={result.mrr:.2f} latency={result.avg_latency_ms:.2f}ms cost={result.token_cost:,.0f} "
            f"({eval_time:.1f}s)"
        )

        artifact = _build_artifact(
            result,
            mode=mode,
            executed_at=executed_at,
            repo_sha=repo_sha,
            haystack_length=haystack_length,
            total_tokens=total_tokens,
            artefact_schema_version=artefact_schema_version,
        )
        mode_dir = output_root / "RULER256K" / "phase1-v1" / mode
        mode_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = mode_dir / f"{executed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
        artifact_path.write_text(
            json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"  Wrote {artifact_path}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a minimal synthetic RULER 256K benchmark.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--haystack-length", type=int, default=HAYSTACK_LENGTH)
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--artefact-schema-version", default="1.0.0")
    args = parser.parse_args()

    run_benchmark(
        output_root=args.output_root,
        haystack_length=args.haystack_length,
        top_k=args.top_k,
        seed=args.seed,
        artefact_schema_version=args.artefact_schema_version,
    )


if __name__ == "__main__":
    main()
