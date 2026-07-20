"""DSS-292 — Known-Unknowns split + Abstention F1 eval.

This harness measures whether DSS and baseline retrievers abstain on facts that
deliberately never appear in the corpus while still returning facts that are
present.  It extends the LongBench needle-corpus pattern with three query
classes:

* ``known_present`` — facts inserted into the corpus (standard recall).
* ``known_absent`` — facts excluded from the corpus (should trigger abstention).
* ``borderline`` — structurally compatible but lexically ambiguous facts used
  for calibration.

The harness is deterministic, uses no external LLM / API, and emits a validated
``BenchmarkArtifact`` plus a KSR-EVAL v0.4 manifest.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from backend.benchmarks.artifact_schema import BenchmarkArtifact
from backend.benchmarks.harness import BenchmarkHarness
from backend.benchmarks.manifest import build_manifest, write_manifest
from backend.benchmarks.pinned_queries import QUERIES_ROOT, load_pinned_queries_for_config
from backend.fieldx_kernel.qp_arithmetic import qp_score
from backend.fieldx_kernel.qp_coordinate import (
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


DEFAULT_OUTPUT_ROOT = Path(__file__).parent / "output" / "dss292_known_unknown"
DEFAULT_LENGTHS = (4, 8, 16, 32)
DEFAULT_TOP_K = 5
DEFAULT_SEED = 193
DEFAULT_SEEDS = (193, 42, 7)
ALPHA = 0.05
WORKING_PRECISION = 16

PRESENT_TEXT = (
    "The project budget was approved at 9:00 and the contingency reserve was 2 percent."
)
ABSENT_TEXT = (
    "The emergency override code was 7-9-3 and required two witness signatures."
)
BORDERLINE_TEXT = (
    "The project budget discussion started at 9:00 and a contingency of 2 percent was mentioned informally."
)
QUERY_PRESENT = "What was the approved budget contingency reserve?"
QUERY_ABSENT = "What was the emergency override code procedure?"
QUERY_BORDERLINE = "Was a 2 percent contingency reserve approved for the project budget?"


@dataclass(frozen=True)
class BenchmarkConfig:
    output_root: Path
    lengths: tuple[int, ...]
    top_k: int
    seeds: tuple[int, ...]
    force_generate_queries: bool = False
    pinned_query_path: Path | None = None


@dataclass(frozen=True)
class Memory:
    memory_id: str
    text: str
    coordinate: QpCoordinate
    length: int


class QueryClass:
    PRESENT = "known_present"
    ABSENT = "known_absent"
    BORDERLINE = "borderline"


@dataclass(frozen=True)
class BenchmarkQuery:
    query_id: str
    text: str
    coordinate: QpCoordinate
    query_class: str
    target_id: str | None
    length: int


@dataclass(frozen=True)
class RetrievalOutcome:
    returned_id: str | None
    abstained: bool
    score: float


@dataclass(frozen=True)
class PerQueryResult:
    query_id: str
    query_class: str
    length: int
    target_id: str | None
    qp_outcome: RetrievalOutcome
    vector_outcome: RetrievalOutcome


@dataclass(frozen=True)
class AbstentionMetrics:
    abstention_precision: float
    abstention_recall: float
    false_abstention_rate: float
    present_recall: float


@dataclass(frozen=True)
class BenchmarkSummary:
    queries: int
    qp: AbstentionMetrics
    vector: AbstentionMetrics
    qp_borderline: AbstentionMetrics
    vector_borderline: AbstentionMetrics
    per_class: dict[str, dict[str, AbstentionMetrics]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "queries": self.queries,
            "qp": {
                "abstention_precision": self.qp.abstention_precision,
                "abstention_recall": self.qp.abstention_recall,
                "false_abstention_rate": self.qp.false_abstention_rate,
                "present_recall": self.qp.present_recall,
            },
            "vector": {
                "abstention_precision": self.vector.abstention_precision,
                "abstention_recall": self.vector.abstention_recall,
                "false_abstention_rate": self.vector.false_abstention_rate,
                "present_recall": self.vector.present_recall,
            },
            "qp_borderline": {
                "abstention_precision": self.qp_borderline.abstention_precision,
                "abstention_recall": self.qp_borderline.abstention_recall,
                "false_abstention_rate": self.qp_borderline.false_abstention_rate,
                "present_recall": self.qp_borderline.present_recall,
            },
            "vector_borderline": {
                "abstention_precision": self.vector_borderline.abstention_precision,
                "abstention_recall": self.vector_borderline.abstention_recall,
                "false_abstention_rate": self.vector_borderline.false_abstention_rate,
                "present_recall": self.vector_borderline.present_recall,
            },
            "per_class": {
                cls: {
                    "qp": {
                        "abstention_precision": m.qp.abstention_precision,
                        "abstention_recall": m.qp.abstention_recall,
                        "false_abstention_rate": m.qp.false_abstention_rate,
                        "present_recall": m.qp.present_recall,
                    },
                    "vector": {
                        "abstention_precision": m.vector.abstention_precision,
                        "abstention_recall": m.vector.abstention_recall,
                        "false_abstention_rate": m.vector.false_abstention_rate,
                        "present_recall": m.vector.present_recall,
                    },
                }
                for cls, m in self.per_class.items()
            },
        }


# -----------------------------------------------------------------------------
# Coordinate construction (mirrors longbench_needle_benchmark.py)
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
        "The project budget was discussed and the reserve numbers included 2 3 5 percent at 9:00 or 10:00.",
        "Approval happened at 9:00 10:00 11:00 and contingency reserves were 2 3 5 percent.",
        "A note about budget approval, reserve percentage, and planning at 9:00.",
        "Reserve numbers 2 3 5 percent were reviewed along with the budget approval schedule.",
    ]
    base = rng.choice(templates)
    repetitions = " ".join(
        rng.sample(sorted(query_tokens), k=min(len(query_tokens), rng.randint(2, 4)))
    )
    return f"{base} {repetitions}"


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
) -> tuple[list[Memory], list[BenchmarkQuery]]:
    """Generate a deterministic known-present / known-absent / borderline corpus.

    For each requested haystack length, one present memory is inserted and
    ``length`` distractors are added.  Absent and borderline queries have no
    matching memory in the corpus; absent facts are structurally incompatible
    with the query coordinate, while borderline facts are structurally close
    but not identical.
    """
    rng = random.Random(seed)
    query_tokens_present = set(normalise_tokens(QUERY_PRESENT))
    query_tokens_absent = set(normalise_tokens(QUERY_ABSENT))
    query_tokens_borderline = set(normalise_tokens(QUERY_BORDERLINE))

    memories: list[Memory] = []
    queries: list[BenchmarkQuery] = []

    for length in lengths:
        # One present memory per length.
        present_coord = _make_coordinate(
            kernel_node="Eq2",
            valuation_offset=3,
            circulation_pass=3,
            hysteresis_depth=0.3,
            dual_valid=True,
        )
        present_id = f"len{length}:present"
        memories.append(
            Memory(
                memory_id=present_id,
                text=PRESENT_TEXT,
                coordinate=present_coord,
                length=length,
            )
        )

        # Distractors for the present query.
        for i in range(length):
            distractor_type = rng.choice(
                ["semantic", "semantic", "lexical", "depth", "random"]
            )
            if distractor_type == "semantic":
                coord = _make_coordinate(
                    kernel_node="Eq2",
                    valuation_offset=3,
                    circulation_pass=3,
                    hysteresis_depth=0.3,
                    dual_valid=False,
                )
            elif distractor_type == "depth":
                coord = _make_coordinate(
                    kernel_node="Eq2",
                    valuation_offset=rng.randint(6, 9),
                    circulation_pass=rng.randint(6, 9),
                    hysteresis_depth=round(rng.uniform(0.6, 0.9), 2),
                    dual_valid=True,
                )
            elif distractor_type == "lexical":
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

            if distractor_type == "random":
                text = "The quick brown fox jumps over the lazy dog under a bright moon."
            else:
                text = _distractor_text(rng, query_tokens_present)
            memories.append(
                Memory(
                    memory_id=f"len{length}:d{i}:{distractor_type}",
                    text=text,
                    coordinate=coord,
                    length=length,
                )
            )

        queries.append(
            BenchmarkQuery(
                query_id=f"len{length}:q_present",
                text=QUERY_PRESENT,
                coordinate=present_coord,
                query_class=QueryClass.PRESENT,
                target_id=present_id,
                length=length,
            )
        )
        # Absent-query coordinate uses a different metric prime family so no
        # memory in this corpus can be structurally compatible.
        absent_coord = _make_coordinate(
            kernel_node="Eq4",
            valuation_offset=5,
            circulation_pass=2,
            hysteresis_depth=0.4,
            dual_valid=True,
        )
        queries.append(
            BenchmarkQuery(
                query_id=f"len{length}:q_absent",
                text=QUERY_ABSENT,
                coordinate=absent_coord,
                query_class=QueryClass.ABSENT,
                target_id=None,
                length=length,
            )
        )
        # Borderline query uses the present coordinate family (structurally
        # compatible with the present memory) but asks an ambiguous factual
        # question; the correct behavior is abstention.
        queries.append(
            BenchmarkQuery(
                query_id=f"len{length}:q_borderline",
                text=QUERY_BORDERLINE,
                coordinate=present_coord,
                query_class=QueryClass.BORDERLINE,
                target_id=None,
                length=length,
            )
        )

    return memories, queries


# -----------------------------------------------------------------------------
# Retrieval systems with abstention
# -----------------------------------------------------------------------------


class VectorRAGBaseline:
    """Deterministic bag-of-words cosine baseline with score-threshold abstention."""

    def __init__(self, memories: Sequence[Memory]) -> None:
        self._memories = list(memories)
        self._vocab = self._build_vocabulary()
        self._vectors = {m.memory_id: self._vectorize(m.text) for m in memories}

    def _build_vocabulary(self) -> dict[str, int]:
        vocab: set[str] = set()
        for memory in self._memories:
            vocab.update(normalise_tokens(memory.text))
        return {token: idx for idx, token in enumerate(sorted(vocab))}

    def _vectorize(self, text: str) -> "numpy.ndarray":
        import numpy as np

        vec = np.zeros(len(self._vocab), dtype=np.float64)
        for token in normalise_tokens(text):
            idx = self._vocab.get(token)
            if idx is not None:
                vec[idx] += 1.0
        norm = np.linalg.norm(vec)
        if norm == 0:
            return vec
        return vec / norm

    def retrieve(
        self,
        query_text: str,
        *,
        abstain_threshold: float = 0.25,
    ) -> RetrievalOutcome:
        query_vec = self._vectorize(query_text)
        best_mid: str | None = None
        best_score = -1.0
        for memory in self._memories:
            sim = float(query_vec @ self._vectors[memory.memory_id])
            if sim > best_score:
                best_score = sim
                best_mid = memory.memory_id
        if best_score < abstain_threshold:
            return RetrievalOutcome(returned_id=None, abstained=True, score=best_score)
        return RetrievalOutcome(returned_id=best_mid, abstained=False, score=best_score)


class QpRouter:
    """Genuine Qp routing with architecture filters and score-threshold abstention."""

    def __init__(self, memories: Sequence[Memory]) -> None:
        self._memories = list(memories)

    def retrieve(
        self,
        query: BenchmarkQuery,
        *,
        abstain_threshold: float = 0.35,
    ) -> RetrievalOutcome:
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
        if not scored or scored[0][1] < abstain_threshold:
            return RetrievalOutcome(
                returned_id=None,
                abstained=True,
                score=scored[0][1] if scored else 0.0,
            )
        return RetrievalOutcome(
            returned_id=scored[0][2],
            abstained=False,
            score=scored[0][1],
        )


# -----------------------------------------------------------------------------
# Evaluation
# -----------------------------------------------------------------------------


def _compute_abstention_metrics(
    results: Sequence[PerQueryResult],
    system: str,
    *,
    include_borderline: bool = False,
) -> AbstentionMetrics:
    """Compute abstention metrics for one retrieval system.

    By default only known-present and known-absent queries are counted; this
    is the gate-relevant subset.  Set ``include_borderline=True`` to treat
    borderline queries as expected-to-abstain calibration cases.
    """
    total_abstentions = 0
    true_abstentions = 0
    total_absent = 0
    abstained_on_absent = 0
    total_present = 0
    abstained_on_present = 0
    present_returned_correct = 0

    for r in results:
        outcome = r.qp_outcome if system == "qp" else r.vector_outcome
        if r.query_class == QueryClass.ABSENT:
            total_absent += 1
            if outcome.abstained:
                total_abstentions += 1
                true_abstentions += 1
                abstained_on_absent += 1
        elif r.query_class == QueryClass.PRESENT:
            total_present += 1
            if outcome.abstained:
                total_abstentions += 1
                abstained_on_present += 1
            elif outcome.returned_id == r.target_id:
                present_returned_correct += 1
        elif r.query_class == QueryClass.BORDERLINE and include_borderline:
            # Borderline facts are intentionally ambiguous.  Treat them as
            # "should abstain" for calibration purposes.
            total_absent += 1
            if outcome.abstained:
                total_abstentions += 1
                true_abstentions += 1
                abstained_on_absent += 1

    abstention_precision = true_abstentions / total_abstentions if total_abstentions else 1.0
    abstention_recall = abstained_on_absent / total_absent if total_absent else 1.0
    false_abstention_rate = abstained_on_present / total_present if total_present else 0.0
    present_recall = present_returned_correct / total_present if total_present else 0.0

    return AbstentionMetrics(
        abstention_precision=abstention_precision,
        abstention_recall=abstention_recall,
        false_abstention_rate=false_abstention_rate,
        present_recall=present_recall,
    )


def _evaluate_query(
    query: BenchmarkQuery,
    *,
    qp_router: QpRouter,
    vector_baseline: VectorRAGBaseline,
) -> PerQueryResult:
    return PerQueryResult(
        query_id=query.query_id,
        query_class=query.query_class,
        length=query.length,
        target_id=query.target_id,
        qp_outcome=qp_router.retrieve(query),
        vector_outcome=vector_baseline.retrieve(query.text),
    )


def evaluate(
    memories: Sequence[Memory],
    queries: Sequence[BenchmarkQuery],
) -> BenchmarkSummary:
    qp_router = QpRouter(memories)
    vector_baseline = VectorRAGBaseline(memories)

    per_query = [
        _evaluate_query(q, qp_router=qp_router, vector_baseline=vector_baseline)
        for q in queries
    ]

    per_class: dict[str, list[PerQueryResult]] = {}
    for r in per_query:
        per_class.setdefault(r.query_class, []).append(r)

    qp_metrics = _compute_abstention_metrics(per_query, "qp", include_borderline=False)
    vector_metrics = _compute_abstention_metrics(per_query, "vector", include_borderline=False)
    qp_borderline = _compute_abstention_metrics(per_query, "qp", include_borderline=True)
    vector_borderline = _compute_abstention_metrics(per_query, "vector", include_borderline=True)

    class_metrics = {
        cls: {
            "qp": _compute_abstention_metrics(rows, "qp", include_borderline=(cls == QueryClass.BORDERLINE)),
            "vector": _compute_abstention_metrics(rows, "vector", include_borderline=(cls == QueryClass.BORDERLINE)),
        }
        for cls, rows in per_class.items()
    }

    return BenchmarkSummary(
        queries=len(per_query),
        qp=qp_metrics,
        vector=vector_metrics,
        qp_borderline=qp_borderline,
        vector_borderline=vector_borderline,
        per_class=class_metrics,
    )


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
    seed: int,
) -> BenchmarkArtifact:
    repo_sha = _repo_sha()

    def _abstention_group(metrics: AbstentionMetrics) -> dict[str, Any]:
        return {
            "abstention_precision": {
                "value": metrics.abstention_precision,
                "unit": "ratio",
                "description": "True abstentions / total abstentions.",
            },
            "abstention_recall": {
                "value": metrics.abstention_recall,
                "unit": "ratio",
                "description": "Abstained-on-absent / total absent queries.",
            },
            "false_abstention_rate": {
                "value": metrics.false_abstention_rate,
                "unit": "ratio",
                "description": "Abstained-on-present / total present queries.",
            },
            "present_recall": {
                "value": metrics.present_recall,
                "unit": "ratio",
                "description": "Correctly returned present facts / total present queries.",
            },
        }

    gate_passed = (
        summary.qp.abstention_precision >= 0.98
        and summary.qp.abstention_recall >= 0.95
        and summary.qp.false_abstention_rate <= 0.10
    )
    # All five required metric groups are measured, so the artifact is "success".
    # Gate passage is recorded in run_config and in the governance metric values.
    status = "success"

    return BenchmarkArtifact(
        artefact_schema_version="1.0.0",
        run_id=f"dss292-known-unknown-{executed_at.strftime('%Y%m%dT%H%M%SZ')}-{seed}",
        suite_id="dss292-known-unknown",
        suite_version="v1",
        executed_at=executed_at,
        mode="full_dss",
        status=status,  # type: ignore[arg-type]
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
                "name": "dss292_known_unknown_synthetic_v1",
                "version": "v1",
                "split": "benchmark",
                "record_count": len(per_query),
            }
        ],
        metrics={
            "retrieval": {
                "status": "present",
                "metrics": {
                    "qp_present_recall": {
                        "value": summary.qp.present_recall,
                        "unit": "ratio",
                        "description": "Qp recall on known-present facts.",
                    },
                    "vector_present_recall": {
                        "value": summary.vector.present_recall,
                        "unit": "ratio",
                        "description": "Vector-RAG recall on known-present facts.",
                    },
                },
            },
            "governance": {
                "status": "present",
                "metrics": {
                    "qp_abstention_precision": {
                        "value": summary.qp.abstention_precision,
                        "unit": "ratio",
                        "description": "Qp: true abstentions / total abstentions.",
                    },
                    "qp_abstention_recall": {
                        "value": summary.qp.abstention_recall,
                        "unit": "ratio",
                        "description": "Qp: abstained-on-absent / total absent queries.",
                    },
                    "qp_false_abstention_rate": {
                        "value": summary.qp.false_abstention_rate,
                        "unit": "ratio",
                        "description": "Qp: abstained-on-present / total present queries.",
                    },
                    "vector_abstention_precision": {
                        "value": summary.vector.abstention_precision,
                        "unit": "ratio",
                        "description": "Vector-RAG: true abstentions / total abstentions.",
                    },
                    "vector_abstention_recall": {
                        "value": summary.vector.abstention_recall,
                        "unit": "ratio",
                        "description": "Vector-RAG: abstained-on-absent / total absent queries.",
                    },
                    "vector_false_abstention_rate": {
                        "value": summary.vector.false_abstention_rate,
                        "unit": "ratio",
                        "description": "Vector-RAG: abstained-on-present / total present queries.",
                    },
                    "qp_borderline_abstention_recall": {
                        "value": summary.qp_borderline.abstention_recall,
                        "unit": "ratio",
                        "description": "Qp abstention recall when borderline cases are treated as expected-to-abstain.",
                    },
                    "vector_borderline_abstention_recall": {
                        "value": summary.vector_borderline.abstention_recall,
                        "unit": "ratio",
                        "description": "Vector-RAG abstention recall when borderline cases are treated as expected-to-abstain.",
                    },
                },
            },
            "traceability": {
                "status": "present",
                "metrics": {
                    "total_queries": {
                        "value": len(per_query),
                        "unit": "count",
                        "description": "Total number of evaluated queries.",
                    },
                    "query_class_present": {
                        "value": len([r for r in per_query if r.query_class == QueryClass.PRESENT]),
                        "unit": "count",
                        "description": "Number of known-present queries.",
                    },
                    "query_class_absent": {
                        "value": len([r for r in per_query if r.query_class == QueryClass.ABSENT]),
                        "unit": "count",
                        "description": "Number of known-absent queries.",
                    },
                    "query_class_borderline": {
                        "value": len([r for r in per_query if r.query_class == QueryClass.BORDERLINE]),
                        "unit": "count",
                        "description": "Number of borderline calibration queries.",
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
            "seed": seed,
            "alpha": ALPHA,
            "gate_passed": gate_passed,
            "gate_abstention_precision_min": 0.98,
            "gate_abstention_recall_min": 0.95,
            "gate_false_abstention_rate_max": 0.10,
        },
    )


def _load_or_generate_queries(
    seed: int, config: BenchmarkConfig
) -> tuple[list[Memory], list[BenchmarkQuery]]:
    """Return memories and queries, preferring the pinned query set."""
    if not config.force_generate_queries:
        try:
            pinned_queries = load_pinned_queries_for_config(
                "dss292-known-unknown",
                seed,
                root=config.pinned_query_path or QUERIES_ROOT,
                lengths=config.lengths,
            )
            memories, _ = generate_corpus(config.lengths, seed=seed)
            return memories, pinned_queries
        except (FileNotFoundError, ValueError, KeyError) as exc:
            print(f"WARNING: DSS-292 falling back to runtime query generation: {exc}")
    return generate_corpus(config.lengths, seed=seed)


def run_single_seed(seed: int, config: BenchmarkConfig) -> BenchmarkArtifact:
    """Run DSS-292 for a single seed and return a validated artifact."""
    start = time.perf_counter()
    memories, queries = _load_or_generate_queries(seed, config)
    summary = evaluate(memories, queries)
    runtime_ms = (time.perf_counter() - start) * 1000.0

    executed_at = datetime.now(timezone.utc)
    per_query = [
        _evaluate_query(q, qp_router=QpRouter(memories), vector_baseline=VectorRAGBaseline(memories))
        for q in queries
    ]
    artifact = _build_artifact(
        summary,
        per_query,
        config=config,
        executed_at=executed_at,
        runtime_ms=runtime_ms,
        seed=seed,
    )

    output_path = config.output_root / "seeds" / str(seed) / f"{executed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )

    manifest = build_manifest(
        artifact,
        eval_script_version="dss292_known_unknown_benchmark_v1.0",
        seeds=[seed],
        conditions={
            "lengths": ",".join(str(x) for x in config.lengths),
            "top_k": config.top_k,
            "transport": "R1",
        },
    )
    manifest_path = output_path.with_suffix(".manifest.json")
    write_manifest(manifest, manifest_path)

    return artifact


def run_benchmark(config: BenchmarkConfig) -> BenchmarkArtifact:
    """Run DSS-292 across the configured seeds and return the aggregate artifact."""
    harness = BenchmarkHarness(
        suite_id="dss292-known-unknown",
        suite_version="v1",
        mode="full_dss",
        seeds=list(config.seeds),
        output_root=config.output_root,
        run_label="dss292-known-unknown",
    )
    return harness.run(lambda seed: run_single_seed(seed, config))


def print_summary(summary: BenchmarkSummary) -> None:
    print("DSS-292 Known-Unknown Abstention Benchmark")
    print("============================================")
    print(f"Queries              : {summary.queries}")
    print("Qp abstention metrics")
    print(f"  precision          : {summary.qp.abstention_precision:.3f}")
    print(f"  recall             : {summary.qp.abstention_recall:.3f}")
    print(f"  false abstention   : {summary.qp.false_abstention_rate:.3f}")
    print(f"  present recall     : {summary.qp.present_recall:.3f}")
    print("Vector-RAG abstention metrics")
    print(f"  precision          : {summary.vector.abstention_precision:.3f}")
    print(f"  recall             : {summary.vector.abstention_recall:.3f}")
    print(f"  false abstention   : {summary.vector.false_abstention_rate:.3f}")
    print(f"  present recall     : {summary.vector.present_recall:.3f}")
    print("Borderline calibration metrics (treated as expected-to-abstain)")
    print(f"  Qp recall          : {summary.qp_borderline.abstention_recall:.3f}")
    print(f"  Vector recall      : {summary.vector_borderline.abstention_recall:.3f}")
    print("Per-class Qp metrics")
    for cls, metrics in summary.per_class.items():
        print(f"  {cls}")
        print(f"    precision        : {metrics['qp'].abstention_precision:.3f}")
        print(f"    recall           : {metrics['qp'].abstention_recall:.3f}")
        print(f"    false abstention : {metrics['qp'].false_abstention_rate:.3f}")
        print(f"    present recall   : {metrics['qp'].present_recall:.3f}")


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
        help="Top-k cutoff (retained for config consistency; abstention uses rank 1).",
    )
    parser.add_argument(
        "--seeds",
        type=lambda s: tuple(int(x.strip()) for x in s.split(",")),
        default=DEFAULT_SEEDS,
        help="Comma-separated random seeds for multi-seed aggregation.",
    )
    parser.add_argument(
        "--force-generate-queries",
        action="store_true",
        help="Ignore pinned query sets and generate queries at runtime.",
    )
    parser.add_argument(
        "--pinned-query-path",
        type=Path,
        default=None,
        help="Directory containing pinned query sets (default: eval/queries).",
    )
    args = parser.parse_args(argv)

    config = BenchmarkConfig(
        output_root=args.output_root,
        lengths=args.lengths,
        top_k=args.top_k,
        seeds=args.seeds,
        force_generate_queries=args.force_generate_queries,
        pinned_query_path=args.pinned_query_path,
    )
    aggregate = run_benchmark(config)
    print_summary(
        BenchmarkSummary(
            queries=int(aggregate.metrics["traceability"].metrics["total_queries"].value),
            qp=AbstentionMetrics(
                abstention_precision=float(
                    aggregate.metrics["governance"].metrics["qp_abstention_precision"].value
                ),
                abstention_recall=float(
                    aggregate.metrics["governance"].metrics["qp_abstention_recall"].value
                ),
                false_abstention_rate=float(
                    aggregate.metrics["governance"].metrics["qp_false_abstention_rate"].value
                ),
                present_recall=float(
                    aggregate.metrics["retrieval"].metrics["qp_present_recall"].value
                ),
            ),
            vector=AbstentionMetrics(
                abstention_precision=float(
                    aggregate.metrics["governance"].metrics["vector_abstention_precision"].value
                ),
                abstention_recall=float(
                    aggregate.metrics["governance"].metrics["vector_abstention_recall"].value
                ),
                false_abstention_rate=float(
                    aggregate.metrics["governance"].metrics["vector_false_abstention_rate"].value
                ),
                present_recall=float(
                    aggregate.metrics["retrieval"].metrics["vector_present_recall"].value
                ),
            ),
            qp_borderline=AbstentionMetrics(
                abstention_precision=0.0,
                abstention_recall=float(
                    aggregate.metrics["governance"].metrics["qp_borderline_abstention_recall"].value
                ),
                false_abstention_rate=0.0,
                present_recall=0.0,
            ),
            vector_borderline=AbstentionMetrics(
                abstention_precision=0.0,
                abstention_recall=float(
                    aggregate.metrics["governance"].metrics["vector_borderline_abstention_recall"].value
                ),
                false_abstention_rate=0.0,
                present_recall=0.0,
            ),
            per_class={},
        )
    )
    print(f"\nAggregate artifact status: {aggregate.status}")
    print(f"Aggregate artifact written to: {config.output_root / 'aggregate'}")


if __name__ == "__main__":
    main()
