"""Architecture-aligned retrieval benchmark for Qp vs vector-RAG.

This harness tests whether Qp retrieval preserves structural invariants that
vector similarity cannot see:

1. Dual-pair synchronization — does retrieval avoid candidates whose dual state
   is incompatible with the query?
2. Circulation-depth alignment — does retrieval prefer candidates whose
   circulation pass / hysteresis depth are close to the query's?
3. 336 checksum preservation — does retrieval return candidates that contribute
   to a coherent 336=6×7×8 state (correct dimension, depth, and dual sync)?

Pre-registered test
-------------------
Paired two-tailed permutation test on per-query Precision@5 of
architecture-valid candidates (Qp − vector), alpha = 0.05.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from backend.benchmarks.artifact_schema import BenchmarkArtifact
from backend.fieldx_kernel.qp_arithmetic import QpElement, qp_score
from backend.fieldx_kernel.qp_coordinate import (
    DigitSymbol,
    QpCoordinate,
    _DUAL_COMPLEMENT,
    _TETRAHEDRON,
    _coordinate_hash,
    qp_coordinate_distance,
)
from backend.fieldx_kernel.qp_retrieval import qp_pure_compatible
from backend.search.token_index import normalise_tokens


DEFAULT_CORPUS_PATH = (
    Path(__file__).parent
    / "corpus"
    / "qp_retrieval"
    / "qp_architecture_corpus_v1.jsonl"
)
DEFAULT_OUTPUT_ROOT = Path(__file__).parent / "output" / "qp_architecture"
DEFAULT_TOP_K = 5
DEFAULT_PERMUTATIONS = 10_000
ALPHA = 0.05


@dataclass(frozen=True)
class BenchmarkConfig:
    corpus_path: Path
    output_root: Path
    top_k: int
    permutations: int
    seed: int


@dataclass(frozen=True)
class Memory:
    memory_id: str
    text: str
    coordinate: QpCoordinate
    valid: bool
    task_type: str


@dataclass(frozen=True)
class Query:
    query_id: str
    text: str
    coordinate: QpCoordinate
    task_type: str


@dataclass(frozen=True)
class PerQueryResult:
    query_id: str
    task_type: str
    qp_precision_at_k: dict[int, float]
    vector_precision_at_k: dict[int, float]
    qp_incoherent_rate: float
    vector_incoherent_rate: float


@dataclass(frozen=True)
class BenchmarkSummary:
    queries: int
    qp_precision_at_1: float
    qp_precision_at_5: float
    vector_precision_at_1: float
    vector_precision_at_5: float
    qp_incoherent_rate: float
    vector_incoherent_rate: float
    p_value: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "queries": self.queries,
            "qp_precision_at_1": self.qp_precision_at_1,
            "qp_precision_at_5": self.qp_precision_at_5,
            "vector_precision_at_1": self.vector_precision_at_1,
            "vector_precision_at_5": self.vector_precision_at_5,
            "qp_incoherent_rate": self.qp_incoherent_rate,
            "vector_incoherent_rate": self.vector_incoherent_rate,
            "p_value": self.p_value,
        }


# -----------------------------------------------------------------------------
# Coordinate construction
# -----------------------------------------------------------------------------


def _node_digit(node: str) -> DigitSymbol:
    from backend.fieldx_kernel.qp_coordinate import _NODE_DIGIT

    return _NODE_DIGIT[node]


def _metric_prime_for_node(node: str) -> int:
    from backend.fieldx_kernel.qp_coordinate import metric_prime as _mp

    return _mp(node)


def _make_coordinate(
    *,
    kernel_node: str,
    valuation_offset: int,
    circulation_pass: int = 0,
    hysteresis_depth: float = 0.0,
    dual_valid: bool | None = None,
    working_precision: int = 16,
) -> QpCoordinate:
    """Build a QpCoordinate with controlled depth, pass, and dual state."""
    metric_prime = _metric_prime_for_node(kernel_node)
    digit = _node_digit(kernel_node)
    unit_digits = tuple(digit for _ in range(valuation_offset))
    coordinate_id = _coordinate_hash(metric_prime, valuation_offset, unit_digits)

    # Rational representative: p^valuation_offset (a deep state in this prime).
    rational_value = metric_prime**valuation_offset if valuation_offset >= 0 else 0
    rational_representative = QpElement.from_int(
        metric_prime, rational_value, working_precision
    )

    # Dual state.
    dual_state: QpCoordinate | None = None
    if dual_valid is not None:
        dual_node = _DUAL_COMPLEMENT[kernel_node]
        if not dual_valid:
            # Pick a mismatched dual node to break synchronization.
            dual_node = "Eq7" if dual_node != "Eq7" else "Eq6"
        dual_state = QpCoordinate.origin(
            metric_prime=_metric_prime_for_node(dual_node),
            working_precision=working_precision,
            kernel_node=dual_node,
        )

    # Mediator state (Law/Grace) based on tetrahedron.
    mediator_state: QpCoordinate | None = None
    tetra = _TETRAHEDRON.get(kernel_node, "S1")
    if tetra == "S1":
        mediator_state = QpCoordinate.origin(
            metric_prime=_metric_prime_for_node("Eq8"),
            working_precision=working_precision,
            kernel_node="Eq8",
        )
    elif tetra == "S2":
        mediator_state = QpCoordinate.origin(
            metric_prime=_metric_prime_for_node("Eq9"),
            working_precision=working_precision,
            kernel_node="Eq9",
        )

    return QpCoordinate(
        coordinate_id=coordinate_id,
        kernel_node=kernel_node,
        metric_prime=metric_prime,
        tetrahedron=tetra,
        dual_complement=_DUAL_COMPLEMENT[kernel_node],
        unit_digits=unit_digits,
        valuation_offset=valuation_offset,
        working_precision=working_precision,
        rational_representative=rational_representative,
        circulation_pass=circulation_pass,
        hysteresis_depth=hysteresis_depth,
        dual_state=dual_state,
        mediator_state=mediator_state,
    )


# -----------------------------------------------------------------------------
# Corpus loading
# -----------------------------------------------------------------------------


def _memory_from_spec(
    entity: str, spec: Mapping[str, Any], task_type: str
) -> Memory:
    coord = _make_coordinate(
        kernel_node=str(spec["kernel_node"]),
        valuation_offset=int(spec["valuation_offset"]),
        circulation_pass=int(spec.get("circulation_pass", 0)),
        hysteresis_depth=float(spec.get("hysteresis_depth", 0.0)),
        dual_valid=spec.get("dual_valid"),
    )
    return Memory(
        memory_id=f"{entity}:{spec['id']}",
        text=str(spec["text"]),
        coordinate=coord,
        valid=bool(spec.get("valid", True)),
        task_type=task_type,
    )


def _query_from_spec(
    entity: str, idx: int, spec: Mapping[str, Any], task_type: str
) -> Query:
    coord = _make_coordinate(
        kernel_node=str(spec["kernel_node"]),
        valuation_offset=int(spec["valuation_offset"]),
        circulation_pass=int(spec.get("circulation_pass", 0)),
        hysteresis_depth=float(spec.get("hysteresis_depth", 0.0)),
        dual_valid=spec.get("dual_valid"),
    )
    return Query(
        query_id=f"{entity}:q{idx}",
        text=str(spec["query"]),
        coordinate=coord,
        task_type=task_type,
    )


def load_corpus(path: Path) -> tuple[list[Memory], list[Query]]:
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
            task_type = str(record["task_type"])
            entity = str(record.get("entity", task_type))

            for mem in record.get("memories", []):
                memories.append(_memory_from_spec(entity, mem, task_type))

            for idx, q in enumerate(record.get("queries", [])):
                queries.append(_query_from_spec(entity, idx, q, task_type))

    return memories, queries


# -----------------------------------------------------------------------------
# Baselines
# -----------------------------------------------------------------------------


class VectorRAGBaseline:
    """Deterministic bag-of-words cosine nearest-neighbour baseline."""

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
            sim = float(np.dot(query_vec, self._vectors[memory.memory_id]))
            scored.append((memory.memory_id, sim))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]


class QpRouter:
    """Genuine Qp routing with architecture filters enabled."""

    def __init__(self, memories: Sequence[Memory]) -> None:
        self._memories = list(memories)

    def rank(self, query: Query, top_k: int) -> list[tuple[str, float]]:
        scored: list[tuple[float, float, str]] = []
        for memory in self._memories:
            if not qp_pure_compatible(query.coordinate, memory.coordinate):
                continue
            try:
                distance = float(qp_coordinate_distance(query.coordinate, memory.coordinate))
            except Exception:
                continue
            score = float(
                qp_score(distance, query.coordinate.metric_prime, query.coordinate.working_precision)
            )
            scored.append((distance, score, memory.memory_id))
        scored.sort(key=lambda triple: (triple[0], -triple[1]))
        return [(mid, score) for _, score, mid in scored[:top_k]]


# -----------------------------------------------------------------------------
# Evaluation
# -----------------------------------------------------------------------------


def _precision_at_k(ranked_ids: Sequence[str], memories: Mapping[str, Memory], k: int) -> float:
    retrieved = [memories[mid] for mid in ranked_ids[:k] if mid in memories]
    if not retrieved:
        return 0.0
    return sum(1 for m in retrieved if m.valid) / len(retrieved)


def _incoherent_rate(ranked_ids: Sequence[str], memories: Mapping[str, Memory]) -> float:
    retrieved = [memories[mid] for mid in ranked_ids if mid in memories]
    if not retrieved:
        return 0.0
    return sum(1 for m in retrieved if not m.valid) / len(retrieved)


def _evaluate_query(
    query: Query,
    *,
    qp_router: QpRouter,
    vector_baseline: VectorRAGBaseline,
    memories_by_id: Mapping[str, Memory],
    top_k: int,
) -> PerQueryResult:
    qp_ranked = [mid for mid, _ in qp_router.rank(query, top_k)]
    vector_ranked = [mid for mid, _ in vector_baseline.rank(query.text, top_k)]

    return PerQueryResult(
        query_id=query.query_id,
        task_type=query.task_type,
        qp_precision_at_k={
            k: _precision_at_k(qp_ranked, memories_by_id, k) for k in (1, 5)
        },
        vector_precision_at_k={
            k: _precision_at_k(vector_ranked, memories_by_id, k) for k in (1, 5)
        },
        qp_incoherent_rate=_incoherent_rate(qp_ranked, memories_by_id),
        vector_incoherent_rate=_incoherent_rate(vector_ranked, memories_by_id),
    )


def _permutation_test_pvalue(
    differences: Sequence[float], permutations: int, seed: int
) -> float | None:
    if len(differences) < 2:
        return None
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
    permutations: int,
    seed: int,
) -> tuple[BenchmarkSummary, list[PerQueryResult]]:
    qp_router = QpRouter(memories)
    vector_baseline = VectorRAGBaseline(memories)
    memories_by_id = {m.memory_id: m for m in memories}

    per_query = [
        _evaluate_query(
            q,
            qp_router=qp_router,
            vector_baseline=vector_baseline,
            memories_by_id=memories_by_id,
            top_k=top_k,
        )
        for q in queries
    ]

    differences = [
        r.qp_precision_at_k[5] - r.vector_precision_at_k[5] for r in per_query
    ]
    p_value = _permutation_test_pvalue(differences, permutations, seed)

    def _mean(getter: callable) -> float:
        values = [getter(r) for r in per_query]
        return statistics.mean(values) if values else 0.0

    summary = BenchmarkSummary(
        queries=len(per_query),
        qp_precision_at_1=_mean(lambda r: r.qp_precision_at_k[1]),
        qp_precision_at_5=_mean(lambda r: r.qp_precision_at_k[5]),
        vector_precision_at_1=_mean(lambda r: r.vector_precision_at_k[1]),
        vector_precision_at_5=_mean(lambda r: r.vector_precision_at_k[5]),
        qp_incoherent_rate=_mean(lambda r: r.qp_incoherent_rate),
        vector_incoherent_rate=_mean(lambda r: r.vector_incoherent_rate),
        p_value=p_value,
    )
    return summary, per_query


# -----------------------------------------------------------------------------
# Artifact and CLI
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
    per_query: Sequence[PerQueryResult],
    *,
    config: BenchmarkConfig,
    executed_at: datetime,
) -> BenchmarkArtifact:
    repo_sha = _repo_sha()
    return BenchmarkArtifact(
        artefact_schema_version="1.0.0",
        run_id=f"qp-architecture-{executed_at.strftime('%Y%m%dT%H%M%SZ')}",
        suite_id="qp-architecture",
        suite_version="v1",
        executed_at=executed_at,
        mode="coordinate_guided",
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
                "name": config.corpus_path.stem,
                "version": "v1",
                "split": "benchmark",
                "record_count": len(per_query),
            }
        ],
        metrics={
            "retrieval": {
                "status": "present",
                "metrics": {
                    "qp_precision_at_1": {
                        "value": summary.qp_precision_at_1,
                        "unit": "ratio",
                        "description": "Qp Precision@1 of architecture-valid candidates.",
                    },
                    "qp_precision_at_5": {
                        "value": summary.qp_precision_at_5,
                        "unit": "ratio",
                        "description": "Qp Precision@5 of architecture-valid candidates.",
                    },
                    "vector_precision_at_1": {
                        "value": summary.vector_precision_at_1,
                        "unit": "ratio",
                        "description": "Vector-RAG Precision@1 of architecture-valid candidates.",
                    },
                    "vector_precision_at_5": {
                        "value": summary.vector_precision_at_5,
                        "unit": "ratio",
                        "description": "Vector-RAG Precision@5 of architecture-valid candidates.",
                    },
                    "qp_incoherent_rate": {
                        "value": summary.qp_incoherent_rate,
                        "unit": "ratio",
                        "description": "Fraction of Qp retrievals that violate architecture invariants.",
                    },
                    "vector_incoherent_rate": {
                        "value": summary.vector_incoherent_rate,
                        "unit": "ratio",
                        "description": "Fraction of vector-RAG retrievals that violate architecture invariants.",
                    },
                    "p_value": {
                        "value": summary.p_value if summary.p_value is not None else -1.0,
                        "unit": "ratio",
                        "description": "Two-tailed paired permutation-test p-value for Precision@5 difference.",
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
            "seed": config.seed,
            "alpha": ALPHA,
        },
    )


def run_benchmark(config: BenchmarkConfig) -> tuple[BenchmarkSummary, list[PerQueryResult], Path]:
    start = time.perf_counter()
    memories, queries = load_corpus(config.corpus_path)
    summary, per_query = evaluate(
        memories,
        queries,
        top_k=config.top_k,
        permutations=config.permutations,
        seed=config.seed,
    )
    runtime_ms = (time.perf_counter() - start) * 1000.0

    executed_at = datetime.now(timezone.utc)
    artifact = _build_artifact(summary, per_query, config=config, executed_at=executed_at)
    artifact.metrics["latency"].metrics["total_runtime_ms"].value = runtime_ms

    output_path = config.output_root / f"{executed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return summary, per_query, output_path


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Qp architecture-aligned retrieval benchmark")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--permutations", type=int, default=DEFAULT_PERMUTATIONS)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    config = BenchmarkConfig(
        corpus_path=args.corpus,
        output_root=args.output_root,
        top_k=args.top_k,
        permutations=args.permutations,
        seed=args.seed,
    )
    summary, per_query, output_path = run_benchmark(config)

    print(f"Architecture benchmark complete: {output_path}")
    print(json.dumps(summary.as_dict(), indent=2))
    print(f"Per-query results: {len(per_query)}")


if __name__ == "__main__":
    main()
