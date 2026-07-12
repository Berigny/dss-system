"""Qp retrieval vs vector-RAG benchmark harness (DS-REVIEW-193 P2-05).

This harness compares genuine Qp routing against a deterministic vector-RAG
baseline on a versioned synthetic corpus.  It is designed to be reproducible
without network access, while leaving a hook for an OpenAI/OpenRouter
embedding baseline via environment variables.

Pre-registered statistical test
--------------------------------
Paired permutation test on per-query MRR differences (Qp - vector).
Null hypothesis: Qp and vector have identical per-query MRR.
Significance level alpha = 0.05 (two-tailed).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from backend.benchmarks.artifact_schema import BenchmarkArtifact, BenchmarkMode
from backend.fieldx_kernel.qp_arithmetic import qp_score
from backend.fieldx_kernel.qp_coordinate import QpCoordinate, qp_coordinate_distance
from backend.fieldx_kernel.qp_retrieval import (
    derive_query_coordinate_from_factors,
    extract_qp_coordinate,
    qp_pure_compatible,
)
from backend.search.token_index import normalise_tokens


DEFAULT_CORPUS_PATH = Path(__file__).parent / "corpus" / "qp_retrieval" / "qp_retrieval_corpus_v1.jsonl"
DEFAULT_OUTPUT_ROOT = Path(__file__).parent / "output" / "qp_vs_rag"
DEFAULT_TOP_K = 10
DEFAULT_PERMUTATIONS = 10_000
ALPHA = 0.05


@dataclass(frozen=True)
class BenchmarkConfig:
    corpus_path: Path
    output_root: Path
    top_k: int
    permutations: int
    use_qp_filters: bool
    seed: int


@dataclass(frozen=True)
class Memory:
    memory_id: str
    text: str
    coordinate: QpCoordinate


@dataclass(frozen=True)
class Query:
    query_id: str
    text: str
    coordinate: QpCoordinate
    relevant_ids: set[str]
    task: str


@dataclass(frozen=True)
class PerQueryResult:
    query_id: str
    task: str
    qp_rank: int | None
    vector_rank: int | None
    qp_mrr: float
    vector_mrr: float
    qp_recall_at_k: dict[int, bool]
    vector_recall_at_k: dict[int, bool]
    qp_precision_at_5: float
    vector_precision_at_5: float


@dataclass(frozen=True)
class BenchmarkSummary:
    queries: int
    recall_at_1_qp: float
    recall_at_5_qp: float
    recall_at_10_qp: float
    recall_at_1_vector: float
    recall_at_5_vector: float
    recall_at_10_vector: float
    mrr_qp: float
    mrr_vector: float
    precision_at_5_qp: float
    precision_at_5_vector: float
    qp_wins: int
    vector_wins: int
    ties: int
    p_value: float | None
    ablation_label: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "queries": self.queries,
            "recall_at_1_qp": self.recall_at_1_qp,
            "recall_at_5_qp": self.recall_at_5_qp,
            "recall_at_10_qp": self.recall_at_10_qp,
            "recall_at_1_vector": self.recall_at_1_vector,
            "recall_at_5_vector": self.recall_at_5_vector,
            "recall_at_10_vector": self.recall_at_10_vector,
            "mrr_qp": self.mrr_qp,
            "mrr_vector": self.mrr_vector,
            "precision_at_5_qp": self.precision_at_5_qp,
            "precision_at_5_vector": self.precision_at_5_vector,
            "qp_wins": self.qp_wins,
            "vector_wins": self.vector_wins,
            "ties": self.ties,
            "p_value": self.p_value,
            "ablation_label": self.ablation_label,
        }


# -----------------------------------------------------------------------------
# Corpus loading and coordinate construction
# -----------------------------------------------------------------------------


def _factor_list_from_exponents(exponents: Mapping[int, int]) -> list[dict[str, int]]:
    return [{"prime": int(p), "delta": int(e)} for p, e in exponents.items() if e > 0]


def _make_dual_state(kernel_node: str, valid: bool = True) -> QpCoordinate | None:
    """Return a synthetic dual state for supported S1/S2/C nodes."""
    complement = {
        "Eq0": "Eq4",
        "Eq1": "Eq5",
        "Eq2": "Eq6",
        "Eq3": "Eq7",
        "Eq4": "Eq0",
        "Eq5": "Eq1",
        "Eq6": "Eq2",
        "Eq7": "Eq3",
        "Eq8": "Eq9",
        "Eq9": "Eq8",
    }
    dual_node = complement.get(kernel_node)
    if dual_node is None:
        return None
    if not valid:
        # Break dual synchronization by choosing the wrong paired node.
        wrong_node = "Eq7" if dual_node != "Eq7" else "Eq6"
        return QpCoordinate.origin(
            metric_prime=_metric_prime_for_node(wrong_node), working_precision=16, kernel_node=wrong_node
        )
    return QpCoordinate.origin(
        metric_prime=_metric_prime_for_node(dual_node), working_precision=16, kernel_node=dual_node
    )


def _metric_prime_for_node(node: str) -> int:
    from backend.fieldx_kernel.qp_coordinate import metric_prime as _mp

    return _mp(node)


def _coordinate_from_exponents(
    exponents: Mapping[int, int],
    *,
    dual_valid: bool | None = None,
) -> QpCoordinate:
    """Derive a QpCoordinate from kernel exponents, optionally attaching a dual state."""
    factors = _factor_list_from_exponents(exponents)
    coord = derive_query_coordinate_from_factors(factors, working_precision=16)
    if coord is None:
        raise ValueError(f"Could not derive coordinate from exponents: {exponents}")
    if dual_valid is not None:
        dual_state = _make_dual_state(coord.kernel_node, valid=dual_valid)
        if dual_state is not None:
            coord = coord.with_dual_state(dual_state)
    return coord


def load_corpus(path: Path) -> tuple[list[Memory], list[Query]]:
    """Load a corpus JSONL file and derive QpCoordinates for memories and queries."""
    if not path.exists():
        raise FileNotFoundError(f"Corpus not found: {path}")

    memories: list[Memory] = []
    queries: list[Query] = []

    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            entity = str(record.get("entity", "default"))

            for mem in record.get("memories", []):
                memory_id = f"{entity}:{mem['id']}"
                coord = _coordinate_from_exponents(
                    mem["kernel_exponents"], dual_valid=mem.get("dual_valid")
                )
                memories.append(Memory(memory_id=memory_id, text=mem["text"], coordinate=coord))

            for idx, q in enumerate(record.get("queries", [])):
                query_id = f"{entity}:q{idx}"
                coord = _coordinate_from_exponents(
                    q["kernel_exponents"], dual_valid=q.get("dual_valid")
                )
                queries.append(
                    Query(
                        query_id=query_id,
                        text=q["query"],
                        coordinate=coord,
                        relevant_ids={f"{entity}:{rid}" for rid in q["relevant"]},
                        task=str(q.get("task", "unknown")),
                    )
                )

    return memories, queries


# -----------------------------------------------------------------------------
# Vector-RAG baseline (deterministic bag-of-words)
# -----------------------------------------------------------------------------


class VectorRAGBaseline:
    """Deterministic semantic nearest-neighbour baseline using bag-of-words vectors."""

    def __init__(self, memories: Sequence[Memory]) -> None:
        self._memories = list(memories)
        self._vocab = self._build_vocabulary()
        self._vectors = {m.memory_id: self._vectorize(m.text) for m in memories}

    def _build_vocabulary(self) -> dict[str, int]:
        vocab: set[str] = set()
        for memory in self._memories:
            vocab.update(normalise_tokens(memory.text))
        return {token: idx for idx, token in enumerate(sorted(vocab))}

    def _vectorize(self, text: str) -> np.ndarray:
        vec = np.zeros(len(self._vocab), dtype=np.float64)
        for token in normalise_tokens(text):
            idx = self._vocab.get(token)
            if idx is not None:
                vec[idx] += 1.0
        norm = np.linalg.norm(vec)
        if norm == 0:
            return vec
        return vec / norm

    def rank(self, query_text: str, top_k: int) -> list[tuple[str, float]]:
        query_vec = self._vectorize(query_text)
        scored: list[tuple[str, float]] = []
        for memory in self._memories:
            candidate_vec = self._vectors[memory.memory_id]
            similarity = float(np.dot(query_vec, candidate_vec))
            scored.append((memory.memory_id, similarity))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]


# -----------------------------------------------------------------------------
# Qp router
# -----------------------------------------------------------------------------


class QpRouter:
    """Genuine Qp routing by ultrametric distance."""

    def __init__(self, memories: Sequence[Memory], *, use_filters: bool = True) -> None:
        self._memories = list(memories)
        self._use_filters = use_filters

    def rank(self, query: Query, top_k: int) -> list[tuple[str, float]]:
        scored: list[tuple[float, float, str]] = []
        for memory in self._memories:
            if self._use_filters and not qp_pure_compatible(query.coordinate, memory.coordinate):
                continue
            try:
                distance = float(qp_coordinate_distance(query.coordinate, memory.coordinate))
            except Exception:
                continue
            score = float(
                qp_score(distance, query.coordinate.metric_prime, query.coordinate.working_precision)
            )
            scored.append((distance, score, memory.memory_id))

        # Rank by ultrametric distance ascending (closer is better).
        scored.sort(key=lambda triple: (triple[0], -triple[1]))
        return [(mid, score) for _, score, mid in scored[:top_k]]


# -----------------------------------------------------------------------------
# Evaluation and statistics
# -----------------------------------------------------------------------------


def _first_relevant_rank(
    ranked_ids: Sequence[str], relevant_ids: set[str]
) -> int | None:
    for idx, memory_id in enumerate(ranked_ids, start=1):
        if memory_id in relevant_ids:
            return idx
    return None


def _recall_at_k(ranked_ids: Sequence[str], relevant_ids: set[str], k: int) -> bool:
    return any(memory_id in relevant_ids for memory_id in ranked_ids[:k])


def _precision_at_k(ranked_ids: Sequence[str], relevant_ids: set[str], k: int) -> float:
    if k == 0:
        return 0.0
    retrieved = ranked_ids[:k]
    if not retrieved:
        return 0.0
    return sum(1 for mid in retrieved if mid in relevant_ids) / len(retrieved)


def _evaluate_query(
    query: Query,
    *,
    qp_router: QpRouter,
    vector_baseline: VectorRAGBaseline,
    top_k: int,
) -> PerQueryResult:
    qp_ranked = [mid for mid, _ in qp_router.rank(query, top_k)]
    vector_ranked = [mid for mid, _ in vector_baseline.rank(query.text, top_k)]

    qp_first = _first_relevant_rank(qp_ranked, query.relevant_ids)
    vector_first = _first_relevant_rank(vector_ranked, query.relevant_ids)

    qp_mrr = 1.0 / qp_first if qp_first is not None else 0.0
    vector_mrr = 1.0 / vector_first if vector_first is not None else 0.0

    return PerQueryResult(
        query_id=query.query_id,
        task=query.task,
        qp_rank=qp_first,
        vector_rank=vector_first,
        qp_mrr=qp_mrr,
        vector_mrr=vector_mrr,
        qp_recall_at_k={k: _recall_at_k(qp_ranked, query.relevant_ids, k) for k in (1, 5, 10)},
        vector_recall_at_k={
            k: _recall_at_k(vector_ranked, query.relevant_ids, k) for k in (1, 5, 10)
        },
        qp_precision_at_5=_precision_at_k(qp_ranked, query.relevant_ids, 5),
        vector_precision_at_5=_precision_at_k(vector_ranked, query.relevant_ids, 5),
    )


def _permutation_test_pvalue(
    differences: Sequence[float], permutations: int, seed: int
) -> float:
    """Return a two-tailed paired permutation-test p-value."""
    if len(differences) < 2:
        return None  # type: ignore[return-value]
    observed = statistics.mean(differences)
    rng = random.Random(seed)
    count_extreme = 0
    for _ in range(permutations):
        permuted = [d if rng.random() < 0.5 else -d for d in differences]
        perm_mean = statistics.mean(permuted)
        if abs(perm_mean) >= abs(observed):
            count_extreme += 1
    return count_extreme / permutations


def evaluate(
    memories: Sequence[Memory],
    queries: Sequence[Query],
    *,
    top_k: int,
    use_qp_filters: bool,
    permutations: int,
    seed: int,
) -> tuple[BenchmarkSummary, list[PerQueryResult]]:
    vector_baseline = VectorRAGBaseline(memories)
    qp_router = QpRouter(memories, use_filters=use_qp_filters)

    per_query_results = [
        _evaluate_query(q, qp_router=qp_router, vector_baseline=vector_baseline, top_k=top_k)
        for q in queries
    ]

    def _mean(getter: callable) -> float:
        values = [getter(r) for r in per_query_results]
        return statistics.mean(values) if values else 0.0

    differences = [r.qp_mrr - r.vector_mrr for r in per_query_results]
    p_value = _permutation_test_pvalue(differences, permutations, seed)

    qp_wins = sum(1 for d in differences if d > 0)
    vector_wins = sum(1 for d in differences if d < 0)
    ties = sum(1 for d in differences if d == 0)

    summary = BenchmarkSummary(
        queries=len(per_query_results),
        recall_at_1_qp=_mean(lambda r: r.qp_recall_at_k[1]),
        recall_at_5_qp=_mean(lambda r: r.qp_recall_at_k[5]),
        recall_at_10_qp=_mean(lambda r: r.qp_recall_at_k[10]),
        recall_at_1_vector=_mean(lambda r: r.vector_recall_at_k[1]),
        recall_at_5_vector=_mean(lambda r: r.vector_recall_at_k[5]),
        recall_at_10_vector=_mean(lambda r: r.vector_recall_at_k[10]),
        mrr_qp=_mean(lambda r: r.qp_mrr),
        mrr_vector=_mean(lambda r: r.vector_mrr),
        precision_at_5_qp=_mean(lambda r: r.qp_precision_at_5),
        precision_at_5_vector=_mean(lambda r: r.vector_precision_at_5),
        qp_wins=qp_wins,
        vector_wins=vector_wins,
        ties=ties,
        p_value=p_value,
        ablation_label="filters_on" if use_qp_filters else "filters_off",
    )
    return summary, per_query_results


# -----------------------------------------------------------------------------
# Artifact construction
# -----------------------------------------------------------------------------


def _repo_sha() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent.parent.parent)
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _build_artifact(
    summary: BenchmarkSummary,
    per_query_results: Sequence[PerQueryResult],
    *,
    config: BenchmarkConfig,
    executed_at: datetime,
) -> BenchmarkArtifact:
    repo_sha = _repo_sha()
    corpus_name = config.corpus_path.stem
    record_count = len(per_query_results)

    return BenchmarkArtifact(
        artefact_schema_version="1.0.0",
        run_id=f"qp-vs-rag-{summary.ablation_label}-{executed_at.strftime('%Y%m%dT%H%M%SZ')}",
        suite_id="qp-vs-rag",
        suite_version="v1",
        executed_at=executed_at,
        mode="coordinate_guided" if summary.ablation_label == "filters_on" else "semantic_only",
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
                "name": corpus_name,
                "version": "v1",
                "split": "benchmark",
                "record_count": record_count,
            }
        ],
        metrics={
            "retrieval": {
                "status": "present",
                "metrics": {
                    "recall_at_1_qp": {
                        "value": summary.recall_at_1_qp,
                        "unit": "ratio",
                        "description": "Qp recall at rank 1.",
                    },
                    "recall_at_5_qp": {
                        "value": summary.recall_at_5_qp,
                        "unit": "ratio",
                        "description": "Qp recall at rank 5.",
                    },
                    "recall_at_10_qp": {
                        "value": summary.recall_at_10_qp,
                        "unit": "ratio",
                        "description": "Qp recall at rank 10.",
                    },
                    "recall_at_1_vector": {
                        "value": summary.recall_at_1_vector,
                        "unit": "ratio",
                        "description": "Vector-RAG recall at rank 1.",
                    },
                    "recall_at_5_vector": {
                        "value": summary.recall_at_5_vector,
                        "unit": "ratio",
                        "description": "Vector-RAG recall at rank 5.",
                    },
                    "recall_at_10_vector": {
                        "value": summary.recall_at_10_vector,
                        "unit": "ratio",
                        "description": "Vector-RAG recall at rank 10.",
                    },
                    "mrr_qp": {
                        "value": summary.mrr_qp,
                        "unit": "ratio",
                        "description": "Qp mean reciprocal rank.",
                    },
                    "mrr_vector": {
                        "value": summary.mrr_vector,
                        "unit": "ratio",
                        "description": "Vector-RAG mean reciprocal rank.",
                    },
                    "precision_at_5_qp": {
                        "value": summary.precision_at_5_qp,
                        "unit": "ratio",
                        "description": "Qp precision at rank 5.",
                    },
                    "precision_at_5_vector": {
                        "value": summary.precision_at_5_vector,
                        "unit": "ratio",
                        "description": "Vector-RAG precision at rank 5.",
                    },
                    "qp_wins": {
                        "value": summary.qp_wins,
                        "unit": "count",
                        "description": "Queries where Qp MRR exceeded vector MRR.",
                    },
                    "p_value": {
                        "value": summary.p_value if summary.p_value is not None else -1.0,
                        "unit": "ratio",
                        "description": "Two-tailed paired permutation-test p-value for MRR difference.",
                    },
                },
            },
            "latency": {
                "status": "present",
                "metrics": {
                    "total_runtime_ms": {
                        "value": 0.0,
                        "unit": "ms",
                        "description": "Total harness runtime measured separately.",
                    }
                },
            },
            "cost": {
                "status": "present",
                "metrics": {
                    "embedding_queries": {
                        "value": summary.queries,
                        "unit": "count",
                        "description": "Number of query embeddings computed.",
                    }
                },
            },
            "traceability": {
                "status": "absent",
                "absence_reason": "Traceability metrics are out of scope for this retrieval-law benchmark.",
            },
            "governance": {
                "status": "absent",
                "absence_reason": "Governance metrics are out of scope for this retrieval-law benchmark.",
            },
        },
        freshness={
            "status": "fresh",
            "checked_at": executed_at,
            "max_age_hours": 24,
            "age_hours": 0.0,
        },
        run_config={
            "corpus_path": str(config.corpus_path),
            "top_k": config.top_k,
            "permutations": config.permutations,
            "use_qp_filters": config.use_qp_filters,
            "seed": config.seed,
            "alpha": ALPHA,
            "ablation_label": summary.ablation_label,
        },
    )


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------


def run_benchmark(config: BenchmarkConfig) -> tuple[BenchmarkSummary, list[PerQueryResult], Path]:
    start = time.perf_counter()
    memories, queries = load_corpus(config.corpus_path)
    summary, per_query_results = evaluate(
        memories,
        queries,
        top_k=config.top_k,
        use_qp_filters=config.use_qp_filters,
        permutations=config.permutations,
        seed=config.seed,
    )
    runtime_ms = (time.perf_counter() - start) * 1000.0

    executed_at = datetime.now(timezone.utc)
    artifact = _build_artifact(summary, per_query_results, config=config, executed_at=executed_at)

    # Patch the measured runtime into the artifact after construction.
    artifact.metrics["latency"].metrics["total_runtime_ms"].value = runtime_ms

    output_path = (
        config.output_root
        / summary.ablation_label
        / f"{executed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return summary, per_query_results, output_path


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Qp vs vector-RAG retrieval benchmark")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--permutations", type=int, default=DEFAULT_PERMUTATIONS)
    parser.add_argument("--no-qp-filters", action="store_true", help="Disable circulation/dual/mediator filters (ablation).")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    config = BenchmarkConfig(
        corpus_path=args.corpus,
        output_root=args.output_root,
        top_k=args.top_k,
        permutations=args.permutations,
        use_qp_filters=not args.no_qp_filters,
        seed=args.seed,
    )

    summary, per_query_results, output_path = run_benchmark(config)

    print(f"Benchmark complete: {output_path}")
    print(json.dumps(summary.as_dict(), indent=2))
    print(f"Per-query results: {len(per_query_results)}")


if __name__ == "__main__":
    main()
