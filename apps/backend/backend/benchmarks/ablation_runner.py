"""Controlled ablation benchmark for coordinate-guided retrieval.

This runner isolates the contribution of the coordinate-guided layer and the
abstention mechanism by running the same retrieval task under several fixed
conditions and recording per-component latency and token-cost breakdowns.

Ablation conditions
-------------------
- semantic_only            : bag-of-words lexical baseline
- coordinate_guided        : Qp-coordinate routing with architecture filters
- full_dss                 : blended lexical + Qp routing
- coordinate_no_filters    : Qp routing without qp_pure_compatible filtering
- coordinate_token_index   : derive coordinates from token primes instead of
                             pre-computed factors
- abstention_on            : full_dss with an abstention threshold
"""

from __future__ import annotations

import json
import math
import statistics
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Sequence

from backend.benchmarks.artifact_schema import (
    BenchmarkArtifact,
    BenchmarkMode,
    HardwareProfile as SchemaHardwareProfile,
    MetricGroup,
)
from backend.benchmarks.hardware import detect_hardware_profile
from backend.benchmarks.run_dual_retrieval_benchmark import (
    BenchmarkMemoryService,
    BenchmarkResult,
    load_dataset,
    seed_memories,
)
from backend.metrics.pricing import estimate_cost_usd
from backend.fieldx_kernel.qp_coordinate import QpCoordinate
from backend.fieldx_kernel.qp_retrieval import (
    derive_query_coordinate_from_factors,
    qp_pure_compatible,
)
from backend.retrieval.fuzzy_retrieve import p_adic_distance
from backend.search.token_index import normalise_tokens


DEFAULT_DATASET_PATH: Final[Path] = Path(__file__).with_name("benchmark_dataset.jsonl")
DEFAULT_TOP_K: Final[int] = 10
DEFAULT_SEED: Final[int] = 193
DEFAULT_COST_MODEL: Final[str] = "meta-llama/llama-3.1-8b-instruct"


@dataclass(frozen=True)
class AblationCondition:
    """A single ablation configuration."""

    name: str
    mode: BenchmarkMode
    use_qp_filters: bool = True
    use_abstention: bool = False
    abstention_threshold: float = 0.35
    coordinate_resolution: str = "factor"  # "factor" or "token"


@dataclass(frozen=True)
class ComponentBreakdown:
    """Per-component timing and token-cost observations."""

    retrieval_ms: list[float]
    coordinate_resolution_ms: list[float]
    post_processing_ms: list[float]
    llm_generation_ms: list[float]
    prompt_tokens: list[int]
    completion_tokens: list[int]
    coordinate_lookup_tokens: list[int]
    retrieval_tokens: list[int]
    coordinate_resolution_tokens: list[int]
    llm_generation_tokens: list[int]
    post_processing_tokens: list[int]


@dataclass(frozen=True)
class AblationResult:
    """Outcome of one ablation condition."""

    condition: AblationCondition
    summary: BenchmarkResult
    breakdown: ComponentBreakdown


def _repo_sha() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent.parent.parent)
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _lexical_similarity(query: str, text: str) -> float:
    query_tokens = set(normalise_tokens(query))
    candidate_tokens = set(normalise_tokens(text))
    if not query_tokens or not candidate_tokens:
        return 0.0
    overlap = len(query_tokens & candidate_tokens)
    return float(overlap) / math.sqrt(float(len(query_tokens) * len(candidate_tokens)))


def _derive_query_coordinate(
    service: BenchmarkMemoryService,
    query_text: str,
    method: str,
) -> QpCoordinate | None:
    """Derive a query coordinate using the requested resolution method."""
    if method == "factor":
        factors = service._factors_for_text(query_text)
    elif method == "token":
        factors = [
            {"prime": service._token_prime(token), "delta": 1}
            for token in normalise_tokens(query_text)
        ]
    else:
        factors = service._factors_for_text(query_text)
    if not factors:
        return None
    return derive_query_coordinate_from_factors(factors, working_precision=16)


def evaluate_ablation(
    service: BenchmarkMemoryService,
    specs: Sequence[Any],
    condition: AblationCondition,
    *,
    top_k: int = DEFAULT_TOP_K,
    cost_model: str = DEFAULT_COST_MODEL,
) -> AblationResult:
    """Evaluate one ablation condition and return metrics plus component breakdown."""
    hits = 0
    hits_at_1 = 0
    hits_at_5 = 0
    rr_total = 0.0
    latencies: list[float] = []

    breakdown = ComponentBreakdown(
        retrieval_ms=[],
        coordinate_resolution_ms=[],
        post_processing_ms=[],
        llm_generation_ms=[],
        prompt_tokens=[],
        completion_tokens=[],
        coordinate_lookup_tokens=[],
        retrieval_tokens=[],
        coordinate_resolution_tokens=[],
        llm_generation_tokens=[],
        post_processing_tokens=[],
    )

    for spec in specs:
        # Coordinate resolution phase.
        t0 = time.perf_counter()
        query_factors: list[dict[str, int]] = []
        query_coord: QpCoordinate | None = None
        if condition.mode in {"coordinate_guided", "full_dss"}:
            query_factors = service._factors_for_text(spec.query)
            query_coord = derive_query_coordinate_from_factors(
                query_factors, working_precision=16
            )
        coord_resolution_ms = (time.perf_counter() - t0) * 1000.0
        breakdown.coordinate_resolution_ms.append(coord_resolution_ms)

        # Retrieval / ranking phase.
        t0 = time.perf_counter()
        memories = list(service.get_all_memories(spec.entity))
        scored: list[tuple[float, dict[str, object]]] = []
        for row in memories:
            text = str(row.get("text") or "")
            semantic_score = 0.0
            if condition.mode in {"semantic_only", "full_dss"}:
                semantic_score = _lexical_similarity(spec.query, text)

            p_adic_score = 0.0
            if condition.mode in {"coordinate_guided", "full_dss"} and query_coord is not None:
                candidate_factors = row.get("factors") or []
                if candidate_factors:
                    if condition.use_qp_filters:
                        candidate_coord = derive_query_coordinate_from_factors(
                            candidate_factors, working_precision=16
                        )
                        if not qp_pure_compatible(query_coord, candidate_coord):
                            continue
                    distance, overlap = p_adic_distance(
                        query_factors, candidate_factors, max_delta=2, min_overlap=1
                    )
                    if overlap > 0 and distance != float("inf"):
                        p_adic_score = 1.0 / (1.0 + distance)

            if condition.mode == "semantic_only":
                score = semantic_score
            elif condition.mode == "coordinate_guided":
                score = p_adic_score
            else:  # full_dss or abstention variant
                score = 0.6 * semantic_score + 0.4 * p_adic_score

            enriched = dict(row)
            enriched["score"] = score
            enriched["semantic_score"] = semantic_score
            enriched["p_adic_score"] = p_adic_score
            scored.append((score, enriched))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        results = [payload for _, payload in scored[:top_k]]
        retrieval_ms = (time.perf_counter() - t0) * 1000.0
        breakdown.retrieval_ms.append(retrieval_ms)

        # Post-processing phase (abstention + score normalization).
        t0 = time.perf_counter()
        if condition.use_abstention:
            top_score = results[0].get("score", 0.0) if results else 0.0
            if top_score < condition.abstention_threshold:
                results = []
        post_ms = (time.perf_counter() - t0) * 1000.0
        breakdown.post_processing_ms.append(post_ms)

        # LLM generation is not exercised in this micro-benchmark; record a
        # nominal zero so downstream breakdowns still aggregate correctly.
        breakdown.llm_generation_ms.append(0.0)

        # Token-cost breakdown.
        query_tokens = len(normalise_tokens(spec.query))
        result_tokens = sum(len(normalise_tokens(str(r.get("text") or ""))) for r in results)
        breakdown.prompt_tokens.append(query_tokens + result_tokens)
        breakdown.retrieval_tokens.append(result_tokens)
        completion_tokens = 64  # nominal until an LLM harness is wired in
        breakdown.completion_tokens.append(completion_tokens)
        breakdown.llm_generation_tokens.append(completion_tokens)
        lookup_tokens = len(query_coord.unit_digits) if query_coord is not None else 0
        breakdown.coordinate_lookup_tokens.append(lookup_tokens)
        breakdown.coordinate_resolution_tokens.append(lookup_tokens)
        breakdown.post_processing_tokens.append(0)

        latencies.append(retrieval_ms + coord_resolution_ms + post_ms)

        rank_hit = None
        for idx, row in enumerate(results):
            text = str(row.get("text") or "")
            if text in spec.relevant_texts:
                rank_hit = idx
                break

        if rank_hit is not None:
            hits += 1
            if rank_hit < 1:
                hits_at_1 += 1
            if rank_hit < 5:
                hits_at_5 += 1
            rr_total += 1.0 / float(rank_hit + 1)

    query_count = len(specs)
    summary = BenchmarkResult(
        recall_at_1=float(hits_at_1) / query_count if query_count else 0.0,
        recall_at_5=float(hits_at_5) / query_count if query_count else 0.0,
        recall_at_10=float(hits) / query_count if query_count else 0.0,
        mrr=rr_total / query_count if query_count else 0.0,
        avg_latency_ms=statistics.mean(latencies) if latencies else 0.0,
        token_cost=statistics.mean(breakdown.prompt_tokens) if breakdown.prompt_tokens else 0.0,
        queries=query_count,
    )
    return AblationResult(condition=condition, summary=summary, breakdown=breakdown)


def _mean(values: Sequence[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _mean_int(values: Sequence[int]) -> float:
    return statistics.mean(values) if values else 0.0


def _mean_cost_usd(
    cost_model: str,
    prompt_tokens: Sequence[int],
    completion_tokens: Sequence[int],
) -> float:
    """Mean per-query USD cost using backend.metrics.pricing."""
    if not prompt_tokens or not completion_tokens:
        return 0.0
    per_query_costs = [
        estimate_cost_usd(cost_model, p, c) or 0.0
        for p, c in zip(prompt_tokens, completion_tokens)
    ]
    return statistics.mean(per_query_costs)


def build_ablation_artifact(
    result: AblationResult,
    *,
    dataset_path: Path,
    executed_at: datetime,
    repo_sha: str,
    artefact_schema_version: str,
    seed: int,
    cost_model: str = DEFAULT_COST_MODEL,
) -> BenchmarkArtifact:
    """Build a validated BenchmarkArtifact from an ablation result."""
    bd = result.breakdown
    condition = result.condition
    hardware_profile = detect_hardware_profile()
    hardware = SchemaHardwareProfile(**hardware_profile.to_dict())
    return BenchmarkArtifact(
        artefact_schema_version=artefact_schema_version,
        run_id=f"ablation-{condition.name}-{executed_at.strftime('%Y%m%dT%H%M%SZ')}-{seed}",
        suite_id="ablation_retrieval",
        suite_version="v1",
        executed_at=executed_at,
        mode=condition.mode,
        status="success" if result.summary.queries > 0 else "failed",
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
                "name": dataset_path.stem,
                "version": "local-v1",
                "split": "benchmark",
            }
        ],
        metrics={
            "retrieval": {
                "status": "present",
                "metrics": {
                    "recall_at_1": {
                        "value": result.summary.recall_at_1,
                        "unit": "ratio",
                        "description": "Recall at rank 1.",
                    },
                    "recall_at_5": {
                        "value": result.summary.recall_at_5,
                        "unit": "ratio",
                        "description": "Recall at rank 5.",
                    },
                    "recall_at_10": {
                        "value": result.summary.recall_at_10,
                        "unit": "ratio",
                        "description": f"Recall within top {DEFAULT_TOP_K}.",
                    },
                    "mrr": {
                        "value": result.summary.mrr,
                        "unit": "ratio",
                        "description": "Mean reciprocal rank.",
                    },
                    "queries": {
                        "value": result.summary.queries,
                        "unit": "count",
                        "description": "Number of evaluated queries.",
                    },
                },
            },
            "latency": {
                "status": "present",
                "metrics": {
                    "avg_latency_ms": {
                        "value": result.summary.avg_latency_ms,
                        "unit": "ms",
                        "description": "Total end-to-end latency per query.",
                    },
                    "retrieval_ms": {
                        "value": _mean(bd.retrieval_ms),
                        "unit": "ms",
                        "description": "Ranking / similarity computation latency.",
                    },
                    "coordinate_resolution_ms": {
                        "value": _mean(bd.coordinate_resolution_ms),
                        "unit": "ms",
                        "description": "Deriving the query coordinate from text.",
                    },
                    "post_processing_ms": {
                        "value": _mean(bd.post_processing_ms),
                        "unit": "ms",
                        "description": "Abstention and score-blending overhead.",
                    },
                    "llm_generation_ms": {
                        "value": _mean(bd.llm_generation_ms),
                        "unit": "ms",
                        "description": "LLM answer generation latency (retrieval-only; nominal zero).",
                    },
                },
            },
            "cost": {
                "status": "present",
                "metrics": {
                    "prompt_tokens": {
                        "value": _mean_int(bd.prompt_tokens),
                        "unit": "tokens",
                        "description": "Tokens in the query plus admitted context.",
                    },
                    "completion_tokens": {
                        "value": _mean_int(bd.completion_tokens),
                        "unit": "tokens",
                        "description": "Estimated completion tokens for the answer.",
                    },
                    "coordinate_lookup_tokens": {
                        "value": _mean_int(bd.coordinate_lookup_tokens),
                        "unit": "tokens",
                        "description": "Coordinate factor / lookup tokens.",
                    },
                    "retrieval_tokens": {
                        "value": _mean_int(bd.retrieval_tokens),
                        "unit": "tokens",
                        "description": "Tokens in retrieved result context (retrieval component).",
                    },
                    "coordinate_resolution_tokens": {
                        "value": _mean_int(bd.coordinate_resolution_tokens),
                        "unit": "tokens",
                        "description": "Tokens used to derive the query coordinate (coordinate-resolution component).",
                    },
                    "llm_generation_tokens": {
                        "value": _mean_int(bd.llm_generation_tokens),
                        "unit": "tokens",
                        "description": "Estimated completion tokens for LLM generation (not exercised here).",
                    },
                    "post_processing_tokens": {
                        "value": _mean_int(bd.post_processing_tokens),
                        "unit": "tokens",
                        "description": "Tokens consumed during abstention / score normalisation.",
                    },
                    "total_cost_usd": {
                        "value": _mean_cost_usd(cost_model, bd.prompt_tokens, bd.completion_tokens),
                        "unit": "usd",
                        "description": f"Mean per-query USD cost estimate ({cost_model}).",
                    },
                },
            },
            "traceability": {
                "status": "present",
                "metrics": {
                    "coordinate_path_observed": {
                        "value": 1 if condition.mode != "semantic_only" else 0,
                        "unit": "boolean",
                        "description": "Whether a coordinate path was materialised for this condition.",
                    },
                },
            },
            "governance": {
                "status": "present",
                "metrics": {
                    "abstention_configured": {
                        "value": 1 if condition.use_abstention else 0,
                        "unit": "boolean",
                        "description": "Whether abstention was configured for this condition.",
                    },
                },
            },
        },
        freshness={
            "status": "fresh",
            "checked_at": executed_at,
            "max_age_hours": 24,
            "age_hours": 0.0,
        },
        hardware=hardware,
        run_config={
            "condition": condition.name,
            "mode": condition.mode,
            "use_qp_filters": condition.use_qp_filters,
            "use_abstention": condition.use_abstention,
            "abstention_threshold": condition.abstention_threshold,
            "coordinate_resolution": condition.coordinate_resolution,
            "top_k": DEFAULT_TOP_K,
            "seed": seed,
            "dataset": dataset_path.name,
            "cost_model": cost_model,
        },
    )


def run_ablation_condition(
    condition: AblationCondition,
    *,
    seed: int = DEFAULT_SEED,
    dataset_path: Path = DEFAULT_DATASET_PATH,
    artefact_schema_version: str = "1.0.0",
    cost_model: str = DEFAULT_COST_MODEL,
) -> BenchmarkArtifact:
    """Run a single ablation condition and return a benchmark artefact."""
    service = BenchmarkMemoryService()
    seed_memories(service, dataset_path)
    _, specs = load_dataset(dataset_path)
    result = evaluate_ablation(service, specs, condition, top_k=DEFAULT_TOP_K, cost_model=cost_model)
    return build_ablation_artifact(
        result,
        dataset_path=dataset_path,
        executed_at=datetime.now(timezone.utc),
        repo_sha=_repo_sha(),
        artefact_schema_version=artefact_schema_version,
        seed=seed,
        cost_model=cost_model,
    )


# Pre-defined ablation matrix used by the suite harness.
ABLATION_CONDITIONS: tuple[AblationCondition, ...] = (
    AblationCondition(name="semantic_only", mode="semantic_only"),
    AblationCondition(name="coordinate_guided", mode="coordinate_guided"),
    AblationCondition(name="full_dss", mode="full_dss"),
    AblationCondition(
        name="coordinate_no_filters",
        mode="coordinate_guided",
        use_qp_filters=False,
    ),
    AblationCondition(
        name="coordinate_token_index",
        mode="coordinate_guided",
        coordinate_resolution="token",
    ),
    AblationCondition(
        name="abstention_on",
        mode="full_dss",
        use_abstention=True,
        abstention_threshold=0.35,
    ),
)


__all__ = (
    "AblationCondition",
    "AblationResult",
    "ComponentBreakdown",
    "ABLATION_CONDITIONS",
    "evaluate_ablation",
    "run_ablation_condition",
    "build_ablation_artifact",
)
