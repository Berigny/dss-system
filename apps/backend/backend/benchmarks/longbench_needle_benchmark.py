"""LongBench-style needle-in-a-haystack retrieval benchmark.

This harness adapts the LongBench/RULER idea used in the earlier Colab notebook
(``Dual_substrate_baselines.ipynb``) to the ds-backend retrieval stack.  Instead
of calling an external LLM, it measures whether Qp coordinate retrieval can
recover a single "needle" memory from a growing haystack of distractors.

The task is the same as the notebook's recall task: find the memory that states
``TIME=9:00`` and ``PRIME=2``.  Qp routing uses the dual-overlay and
circulation-depth filters to discard semantically similar but structurally
incompatible distractors; the vector-RAG baseline ranks by deterministic
bag-of-words cosine and is expected to degrade as the haystack grows.

Metrics
-------
- needle_recall@1 / @5 / @10 per context length and overall
- mean reciprocal rank (MRR)
- average needle rank per length
- paired permutation-test p-value for per-query recall difference (Qp − vector)

Output
------
A validated ``BenchmarkArtifact`` is written under
``backend/benchmarks/output/longbench_needle/``.
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
from backend.fieldx_kernel.qp_arithmetic import qp_score
from backend.fieldx_kernel.qp_coordinate import (
    DigitSymbol,
    QpCoordinate,
    _DUAL_COMPLEMENT,
    _METRIC_PRIME,
    _NODE_DIGIT,
    _TETRAHEDRON,
    _coordinate_hash,
    qp_coordinate_distance,
)
from backend.fieldx_kernel.qp_retrieval import qp_pure_compatible
from backend.search.token_index import normalise_tokens


DEFAULT_OUTPUT_ROOT = Path(__file__).parent / "output" / "longbench_needle"
DEFAULT_LENGTHS = (4, 8, 16, 32, 64, 128, 256)
DEFAULT_TOP_K = 5
DEFAULT_PERMUTATIONS = 5_000
DEFAULT_SEED = 193
ALPHA = 0.05
WORKING_PRECISION = 16

NEEDLE_TEXT = (
    "Meeting time is 9:00 and the smallest prime number discussed was 2."
)
QUERY_TEXT = "What was the meeting time and the smallest prime number discussed?"


@dataclass(frozen=True)
class BenchmarkConfig:
    output_root: Path
    lengths: tuple[int, ...]
    top_k: int
    permutations: int
    seed: int


@dataclass(frozen=True)
class NeedleMemory:
    memory_id: str
    text: str
    coordinate: QpCoordinate
    is_needle: bool
    length: int


@dataclass(frozen=True)
class NeedleQuery:
    query_id: str
    text: str
    coordinate: QpCoordinate
    needle_id: str
    length: int


@dataclass(frozen=True)
class PerLengthResult:
    length: int
    queries: int
    qp_recall_at_1: float
    qp_recall_at_k: float
    vector_recall_at_1: float
    vector_recall_at_k: float
    qp_mrr: float
    vector_mrr: float
    qp_mean_rank: float
    vector_mean_rank: float


@dataclass(frozen=True)
class PerQueryResult:
    query_id: str
    length: int
    qp_rank: int | None
    vector_rank: int | None
    qp_recall_at_k: float
    vector_recall_at_k: float


@dataclass(frozen=True)
class BenchmarkSummary:
    queries: int
    qp_recall_at_1: float
    qp_recall_at_k: float
    vector_recall_at_1: float
    vector_recall_at_k: float
    qp_mrr: float
    vector_mrr: float
    p_value: float | None
    per_length: tuple[PerLengthResult, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "queries": self.queries,
            "qp_recall_at_1": self.qp_recall_at_1,
            "qp_recall_at_k": self.qp_recall_at_k,
            "vector_recall_at_1": self.vector_recall_at_1,
            "vector_recall_at_k": self.vector_recall_at_k,
            "qp_mrr": self.qp_mrr,
            "vector_mrr": self.vector_mrr,
            "p_value": self.p_value,
            "per_length": [r.__dict__ for r in self.per_length],
        }


# -----------------------------------------------------------------------------
# Coordinate construction (mirrors retrieval_architecture_benchmark.py)
# -----------------------------------------------------------------------------


def _make_coordinate(
    *,
    kernel_node: str,
    valuation_offset: int,
    circulation_pass: int = 0,
    hysteresis_depth: float = 0.0,
    dual_valid: bool | None = None,
) -> QpCoordinate:
    """Build a QpCoordinate with controlled depth, pass, and dual state."""
    metric_prime = _METRIC_PRIME[kernel_node]
    digit = _NODE_DIGIT[kernel_node]
    unit_digits = tuple(digit for _ in range(valuation_offset))
    coordinate_id = _coordinate_hash(metric_prime, valuation_offset, unit_digits)

    from backend.fieldx_kernel.qp_arithmetic import QpElement

    rational_value = metric_prime**valuation_offset if valuation_offset >= 0 else 0
    rational_representative = QpElement.from_int(
        metric_prime, rational_value, working_precision=WORKING_PRECISION
    )

    dual_state: QpCoordinate | None = None
    if dual_valid is not None:
        dual_node = _DUAL_COMPLEMENT[kernel_node]
        if not dual_valid:
            # Use a deliberately mismatched dual node to break synchronization.
            dual_node = "Eq7" if dual_node != "Eq7" else "Eq6"
        dual_state = QpCoordinate.origin(
            metric_prime=_METRIC_PRIME[dual_node],
            working_precision=WORKING_PRECISION,
            kernel_node=dual_node,
        )

    mediator_state: QpCoordinate | None = None
    tetra = _TETRAHEDRON.get(kernel_node, "S1")
    if tetra == "S1":
        mediator_state = QpCoordinate.origin(
            metric_prime=_METRIC_PRIME["Eq8"],
            working_precision=WORKING_PRECISION,
            kernel_node="Eq8",
        )
    elif tetra == "S2":
        mediator_state = QpCoordinate.origin(
            metric_prime=_METRIC_PRIME["Eq9"],
            working_precision=WORKING_PRECISION,
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
        working_precision=WORKING_PRECISION,
        rational_representative=rational_representative,
        circulation_pass=circulation_pass,
        hysteresis_depth=hysteresis_depth,
        dual_state=dual_state,
        mediator_state=mediator_state,
    )


# -----------------------------------------------------------------------------
# Synthetic corpus generation
# -----------------------------------------------------------------------------


def _distractor_text(rng: random.Random, query_tokens: set[str]) -> str:
    """Return a distractor that overlaps lexically with the query."""
    templates = [
        "Meeting meeting time time and prime prime numbers including 2 3 5 7 9:00 10:00.",
        "The schedule listed 9:00 10:00 11:00 and prime smallest numbers 2 3 5 7.",
        "Discussed meeting time and prime candidates: 2 3 5 7 at 9:00 or 10:00.",
        "A note about meeting time, smallest prime, and number theory at 9:00.",
        "Prime numbers 2 3 5 7 were reviewed along with the meeting time schedule.",
    ]
    base = rng.choice(templates)
    # Inject query tokens repeatedly to raise the bag-of-words cosine.
    repetitions = " ".join(rng.sample(sorted(query_tokens), k=min(len(query_tokens), rng.randint(2, 4))))
    return f"{base} {repetitions}"


def _query_repeat_text(rng: random.Random) -> str:
    """Return the query text repeated verbatim; this is a strong lexical trap."""
    return " ".join([QUERY_TEXT] * rng.randint(2, 4))


_KNOWN_KERNEL_NODES = tuple(_METRIC_PRIME.keys())


def _random_kernel_node(rng: random.Random, avoid: str | None = None) -> str:
    while True:
        node = rng.choice(_KNOWN_KERNEL_NODES)
        if node != avoid:
            return node


def generate_corpus(
    lengths: Sequence[int] = DEFAULT_LENGTHS,
    *,
    seed: int = DEFAULT_SEED,
) -> tuple[list[NeedleMemory], list[NeedleQuery]]:
    """Generate a deterministic needle-in-haystack corpus.

    For each requested haystack length, one needle is created and ``length``
    distractors are added.  Distractors are designed to be lexically close to the
    query so that a bag-of-words baseline confuses them with the answer, while
    their Qp coordinates violate dual-overlay or circulation-depth constraints.
    """
    rng = random.Random(seed)
    query_tokens = set(normalise_tokens(QUERY_TEXT))

    memories: list[NeedleMemory] = []
    queries: list[NeedleQuery] = []

    for length in lengths:
        needle_coord = _make_coordinate(
            kernel_node="Eq2",
            valuation_offset=3,
            circulation_pass=3,
            hysteresis_depth=0.3,
            dual_valid=True,
        )
        needle_id = f"len{length}:needle"
        memories.append(
            NeedleMemory(
                memory_id=needle_id,
                text=NEEDLE_TEXT,
                coordinate=needle_coord,
                is_needle=True,
                length=length,
            )
        )

        for i in range(length):
            distractor_type = rng.choice(
                ["semantic", "semantic", "query_repeat", "lexical", "depth", "random"]
            )
            if distractor_type == "query_repeat":
                # Lexically identical to the query but structurally invalid.
                coord = _make_coordinate(
                    kernel_node="Eq2",
                    valuation_offset=3,
                    circulation_pass=3,
                    hysteresis_depth=0.3,
                    dual_valid=False,
                )
                text = _query_repeat_text(rng)
            elif distractor_type == "semantic":
                # Same metric and depth, but broken dual state.
                coord = _make_coordinate(
                    kernel_node="Eq2",
                    valuation_offset=3,
                    circulation_pass=3,
                    hysteresis_depth=0.3,
                    dual_valid=False,
                )
            elif distractor_type == "depth":
                # Valid dual, but circulation depth is far from the query's.
                coord = _make_coordinate(
                    kernel_node="Eq2",
                    valuation_offset=rng.randint(6, 9),
                    circulation_pass=rng.randint(6, 9),
                    hysteresis_depth=round(rng.uniform(0.6, 0.9), 2),
                    dual_valid=True,
                )
            elif distractor_type == "lexical":
                # Different metric prime so it cannot satisfy the query's filters.
                coord = _make_coordinate(
                    kernel_node=_random_kernel_node(rng, avoid="Eq2"),
                    valuation_offset=rng.randint(1, 4),
                    circulation_pass=rng.randint(0, 4),
                    hysteresis_depth=round(rng.uniform(0.0, 0.4), 2),
                    dual_valid=None,
                )
            else:  # random
                coord = _make_coordinate(
                    kernel_node=_random_kernel_node(rng),
                    valuation_offset=rng.randint(0, 3),
                    circulation_pass=rng.randint(0, 3),
                    hysteresis_depth=round(rng.uniform(0.0, 0.3), 2),
                    dual_valid=None,
                )

            if distractor_type == "query_repeat":
                text = _query_repeat_text(rng)
            elif distractor_type == "random":
                text = "The quick brown fox jumps over the lazy dog under a bright moon."
            else:
                text = _distractor_text(rng, query_tokens)
            memories.append(
                NeedleMemory(
                    memory_id=f"len{length}:d{i}:{distractor_type}",
                    text=text,
                    coordinate=coord,
                    is_needle=False,
                    length=length,
                )
            )

        queries.append(
            NeedleQuery(
                query_id=f"len{length}:q",
                text=QUERY_TEXT,
                coordinate=needle_coord,
                needle_id=needle_id,
                length=length,
            )
        )

    return memories, queries


# -----------------------------------------------------------------------------
# Baselines
# -----------------------------------------------------------------------------


class VectorRAGBaseline:
    """Deterministic bag-of-words cosine nearest-neighbour baseline."""

    def __init__(self, memories: Sequence[NeedleMemory]) -> None:
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

    def __init__(self, memories: Sequence[NeedleMemory]) -> None:
        self._memories = list(memories)

    def rank(self, query: NeedleQuery, top_k: int) -> list[tuple[str, float]]:
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


def _needle_rank(ranked_ids: Sequence[str], needle_id: str) -> int | None:
    try:
        return ranked_ids.index(needle_id)
    except ValueError:
        return None


def _recall_at_k(rank: int | None, k: int) -> float:
    return 1.0 if rank is not None and rank < k else 0.0


def _mrr(rank: int | None) -> float:
    return 1.0 / (rank + 1) if rank is not None else 0.0


def _evaluate_query(
    query: NeedleQuery,
    *,
    qp_routers: Mapping[int, QpRouter],
    vector_baselines: Mapping[int, VectorRAGBaseline],
    top_k: int,
) -> PerQueryResult:
    qp_router = qp_routers[query.length]
    vector_baseline = vector_baselines[query.length]
    qp_ranked = [mid for mid, _ in qp_router.rank(query, top_k=max(top_k, 10))]
    vector_ranked = [mid for mid, _ in vector_baseline.rank(query.text, top_k=max(top_k, 10))]

    qp_rank = _needle_rank(qp_ranked, query.needle_id)
    vector_rank = _needle_rank(vector_ranked, query.needle_id)

    return PerQueryResult(
        query_id=query.query_id,
        length=query.length,
        qp_rank=qp_rank,
        vector_rank=vector_rank,
        qp_recall_at_k=_recall_at_k(qp_rank, top_k),
        vector_recall_at_k=_recall_at_k(vector_rank, top_k),
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
        if abs(statistics.mean(permuted)) >= abs(observed):
            count_extreme += 1
    return count_extreme / permutations


def evaluate(
    memories: Sequence[NeedleMemory],
    queries: Sequence[NeedleQuery],
    *,
    top_k: int,
    permutations: int,
    seed: int,
) -> tuple[BenchmarkSummary, list[PerQueryResult]]:
    memories_by_length: dict[int, list[NeedleMemory]] = {}
    for memory in memories:
        memories_by_length.setdefault(memory.length, []).append(memory)

    qp_routers = {length: QpRouter(rows) for length, rows in memories_by_length.items()}
    vector_baselines = {
        length: VectorRAGBaseline(rows) for length, rows in memories_by_length.items()
    }

    per_query = [
        _evaluate_query(
            q,
            qp_routers=qp_routers,
            vector_baselines=vector_baselines,
            top_k=top_k,
        )
        for q in queries
    ]

    # Paired test on needle recall@1 differences: Qp is expected to keep the
    # needle at rank 0 while vector-RAG degrades as the haystack grows.
    differences = [
        _recall_at_k(r.qp_rank, 1) - _recall_at_k(r.vector_rank, 1)
        for r in per_query
    ]
    p_value = _permutation_test_pvalue(differences, permutations, seed)

    per_length: list[PerLengthResult] = []
    lengths = sorted({q.length for q in queries})
    for length in lengths:
        rows = [r for r in per_query if r.length == length]
        per_length.append(
            PerLengthResult(
                length=length,
                queries=len(rows),
                qp_recall_at_1=_mean([_recall_at_k(r.qp_rank, 1) for r in rows]),
                qp_recall_at_k=_mean([r.qp_recall_at_k for r in rows]),
                vector_recall_at_1=_mean([_recall_at_k(r.vector_rank, 1) for r in rows]),
                vector_recall_at_k=_mean([r.vector_recall_at_k for r in rows]),
                qp_mrr=_mean([_mrr(r.qp_rank) for r in rows]),
                vector_mrr=_mean([_mrr(r.vector_rank) for r in rows]),
                qp_mean_rank=_mean_rank([r.qp_rank for r in rows]),
                vector_mean_rank=_mean_rank([r.vector_rank for r in rows]),
            )
        )

    summary = BenchmarkSummary(
        queries=len(per_query),
        qp_recall_at_1=_mean([_recall_at_k(r.qp_rank, 1) for r in per_query]),
        qp_recall_at_k=_mean([r.qp_recall_at_k for r in per_query]),
        vector_recall_at_1=_mean([_recall_at_k(r.vector_rank, 1) for r in per_query]),
        vector_recall_at_k=_mean([r.vector_recall_at_k for r in per_query]),
        qp_mrr=_mean([_mrr(r.qp_rank) for r in per_query]),
        vector_mrr=_mean([_mrr(r.vector_rank) for r in per_query]),
        p_value=p_value,
        per_length=tuple(per_length),
    )
    return summary, per_query


def _mean(values: Sequence[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _mean_rank(ranks: Sequence[int | None]) -> float:
    present = [r for r in ranks if r is not None]
    return statistics.mean(present) if present else float("inf")


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
    runtime_ms: float,
) -> BenchmarkArtifact:
    repo_sha = _repo_sha()
    return BenchmarkArtifact(
        artefact_schema_version="1.0.0",
        run_id=f"longbench-needle-{executed_at.strftime('%Y%m%dT%H%M%SZ')}",
        suite_id="longbench-needle",
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
                "name": "longbench_needle_synthetic_v1",
                "version": "v1",
                "split": "benchmark",
                "record_count": len(per_query),
            }
        ],
        metrics={
            "retrieval": {
                "status": "present",
                "metrics": {
                    "qp_recall_at_1": {
                        "value": summary.qp_recall_at_1,
                        "unit": "ratio",
                        "description": "Qp needle recall at rank 1.",
                    },
                    "qp_recall_at_k": {
                        "value": summary.qp_recall_at_k,
                        "unit": "ratio",
                        "description": f"Qp needle recall within top {config.top_k}.",
                    },
                    "vector_recall_at_1": {
                        "value": summary.vector_recall_at_1,
                        "unit": "ratio",
                        "description": "Vector-RAG needle recall at rank 1.",
                    },
                    "vector_recall_at_k": {
                        "value": summary.vector_recall_at_k,
                        "unit": "ratio",
                        "description": f"Vector-RAG needle recall within top {config.top_k}.",
                    },
                    "qp_mrr": {
                        "value": summary.qp_mrr,
                        "unit": "ratio",
                        "description": "Qp mean reciprocal rank.",
                    },
                    "vector_mrr": {
                        "value": summary.vector_mrr,
                        "unit": "ratio",
                        "description": "Vector-RAG mean reciprocal rank.",
                    },
                    "p_value": {
                        "value": summary.p_value if summary.p_value is not None else -1.0,
                        "unit": "ratio",
                        "description": "Two-tailed paired permutation-test p-value for recall@1 difference (Qp - vector).",
                    },
                },
            },
            "latency": {
                "status": "present",
                "metrics": {
                    "total_runtime_ms": {
                        "value": runtime_ms,
                        "unit": "ms",
                        "description": "Total harness runtime.",
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
                "absence_reason": "Traceability metrics are out of scope for this needle-recall benchmark.",
            },
            "governance": {
                "status": "absent",
                "absence_reason": "Governance metrics are out of scope for this needle-recall benchmark.",
            },
        },
        freshness={
            "status": "fresh",
            "checked_at": executed_at,
            "max_age_hours": 24,
            "age_hours": 0.0,
        },
        run_config={
            "lengths": ",".join(str(x) for x in config.lengths),
            "top_k": config.top_k,
            "permutations": config.permutations,
            "seed": config.seed,
            "alpha": ALPHA,
        },
    )


def run_benchmark(config: BenchmarkConfig) -> tuple[BenchmarkSummary, list[PerQueryResult], Path]:
    start = time.perf_counter()
    memories, queries = generate_corpus(config.lengths, seed=config.seed)
    summary, per_query = evaluate(
        memories,
        queries,
        top_k=config.top_k,
        permutations=config.permutations,
        seed=config.seed,
    )
    runtime_ms = (time.perf_counter() - start) * 1000.0

    executed_at = datetime.now(timezone.utc)
    artifact = _build_artifact(summary, per_query, config=config, executed_at=executed_at, runtime_ms=runtime_ms)

    output_path = config.output_root / f"{executed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )

    # Emit KSR-EVAL v0.4 manifest alongside the artifact.
    from backend.benchmarks.manifest import build_manifest, write_manifest

    manifest = build_manifest(
        artifact,
        eval_script_version="longbench_needle_benchmark_v1.0",
        seeds=[config.seed],
        conditions={
            "lengths": ",".join(str(x) for x in config.lengths),
            "top_k": config.top_k,
            "permutations": config.permutations,
            "transport": "R1",
        },
    )
    manifest_path = output_path.with_suffix(".manifest.json")
    write_manifest(manifest, manifest_path)

    return summary, per_query, output_path


def print_summary(summary: BenchmarkSummary, top_k: int) -> None:
    print("LongBench Needle Retrieval Benchmark")
    print("====================================")
    print(f"Queries            : {summary.queries}")
    print(f"Qp recall@1        : {summary.qp_recall_at_1:.3f}")
    print(f"Vector recall@1    : {summary.vector_recall_at_1:.3f}")
    print(f"Qp recall@{top_k:<3}      : {summary.qp_recall_at_k:.3f}")
    print(f"Vector recall@{top_k:<3}  : {summary.vector_recall_at_k:.3f}")
    print(f"Qp MRR             : {summary.qp_mrr:.3f}")
    print(f"Vector MRR         : {summary.vector_mrr:.3f}")
    print(f"p-value            : {summary.p_value}")
    print()
    print("Per-length breakdown")
    print("-" * 60)
    print(f"{'Length':>8} {'Qp@1':>8} {'Vec@1':>8} {'Qp@'+str(top_k):>8} {'Vec@'+str(top_k):>8} {'QpMRR':>8} {'VecMRR':>8}")
    for r in summary.per_length:
        print(
            f"{r.length:>8} {r.qp_recall_at_1:>8.3f} {r.vector_recall_at_1:>8.3f} "
            f"{r.qp_recall_at_k:>8.3f} {r.vector_recall_at_k:>8.3f} "
            f"{r.qp_mrr:>8.3f} {r.vector_mrr:>8.3f}"
        )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for benchmark artifacts.",
    )
    parser.add_argument(
        "--lengths",
        type=lambda s: tuple(int(x.strip()) for x in s.split(",")),
        default=DEFAULT_LENGTHS,
        help="Comma-separated haystack lengths to evaluate.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help="Top-k cutoff for recall metrics.",
    )
    parser.add_argument(
        "--permutations",
        type=int,
        default=DEFAULT_PERMUTATIONS,
        help="Number of permutations for the paired significance test.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Random seed for corpus generation.",
    )
    parser.add_argument(
        "--print-artifact",
        action="store_true",
        help="Print the full benchmark artifact JSON to stdout.",
    )
    args = parser.parse_args(argv)

    config = BenchmarkConfig(
        output_root=args.output_root,
        lengths=args.lengths,
        top_k=args.top_k,
        permutations=args.permutations,
        seed=args.seed,
    )
    summary, per_query, output_path = run_benchmark(config)
    print_summary(summary, config.top_k)
    print(f"\nArtifact written to: {output_path}")

    if args.print_artifact:
        print()
        print(json.dumps(
            _build_artifact(summary, per_query, config=config, executed_at=datetime.now(timezone.utc), runtime_ms=0.0)
            .model_dump(mode="json"),
            indent=2,
        ))


if __name__ == "__main__":
    main()
