"""DSS-299 — Real-data track (Phase R) for RAG retrieval benchmarks.

Loads pinned public QA datasets (HotpotQA, NarrativeQA) via the HuggingFace
``datasets`` library, derives DSS coordinates label-blind from raw text, and
compares DSS against BM25, dense (HNSW), brute-force dense, and long-context
baselines.  The track is gated by the Phase I label-blind coverage gate
(DSS-298) and enforces a token/embedding budget cap.

In dry-run mode or when ``datasets``/network is unavailable, the harness falls
back to a small synthetic corpus so CI stays deterministic and offline.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from backend.benchmarks.artifact_schema import BenchmarkArtifact
from backend.benchmarks.comparison_baselines import BM25Baseline, BaselineResult, LongContextBaseline
from backend.benchmarks.harness import BenchmarkHarness
from backend.benchmarks.hnsw_dense_baseline import HnswDenseBaseline
from backend.benchmarks.manifest import build_manifest, write_manifest
from backend.benchmarks.real_embedding_baseline import PINNED_MODEL_NAME, RealEmbeddingBaseline
from backend.fieldx_kernel.qp_coordinate import QpCoordinate, derive_p_adic_coordinate
from backend.fieldx_kernel.qp_retrieval import qp_pure_compatible
from backend.ingestion.pipeline import ingest_document


DEFAULT_OUTPUT_ROOT = Path(__file__).parent / "output" / "dss299_real_data_track"
DEFAULT_SEEDS = (193, 42, 7, 13, 21)
DEFAULT_TOP_K = 5
DEFAULT_SAMPLES_PER_DATASET = 50
DEFAULT_COVERAGE_GATE = 0.8
DEFAULT_MAX_TOTAL_DOCUMENTS = 1000
DEFAULT_MAX_QUERIES = 100
DEFAULT_MAX_EMBEDDING_CALLS = 2500
DEFAULT_BUDGET_TOKENS = 500_000


@dataclass(frozen=True)
class RealDataDocument:
    doc_id: str
    text: str


@dataclass(frozen=True)
class RealDataExample:
    query_id: str
    query_text: str
    relevant_doc_ids: list[str]
    documents: list[RealDataDocument]
    dataset: str


@dataclass(frozen=True)
class BenchmarkConfig:
    output_root: Path
    datasets: tuple[str, ...]
    samples_per_dataset: int
    top_k: int
    seeds: tuple[int, ...]
    coverage_gate: float
    max_total_documents: int
    max_queries: int
    max_embedding_calls: int
    budget_tokens: int
    skip_real_embedding: bool
    dry_run: bool


@dataclass(frozen=True)
class SystemResult:
    system_name: str
    recall_at_1: float
    recall_at_k: float
    mrr: float
    p_at_1: float | None
    abstention_rate: float | None
    avg_latency_ms: float
    token_cost: float
    precision_at_k: dict[int, float] | None = None
    ndcg_at_k: dict[int, float] | None = None


@dataclass(frozen=True)
class TrackSummary:
    examples: int
    total_documents: int
    total_queries: int
    coverage_score: float
    gate_pass: bool
    systems: dict[str, SystemResult]

    def as_dict(self) -> dict[str, Any]:
        return {
            "examples": self.examples,
            "total_documents": self.total_documents,
            "total_queries": self.total_queries,
            "coverage_score": self.coverage_score,
            "gate_pass": self.gate_pass,
            "systems": {
                name: {
                    "recall_at_1": r.recall_at_1,
                    "recall_at_k": r.recall_at_k,
                    "mrr": r.mrr,
                    "p_at_1": r.p_at_1,
                    "abstention_rate": r.abstention_rate,
                    "avg_latency_ms": r.avg_latency_ms,
                    "token_cost": r.token_cost,
                }
                for name, r in self.systems.items()
            },
        }


# ---------------------------------------------------------------------------
# Label-blind coordinate derivation (same contract as DSS-298)
# ---------------------------------------------------------------------------


def _derive_coordinate(text: str) -> QpCoordinate | None:
    """Derive a QpCoordinate from raw text without any pre-existing label."""
    try:
        result = ingest_document(text)
    except Exception:
        return None
    exponents = result.composite_exponents
    if not any(v > 0 for v in exponents.values()):
        return None
    try:
        return derive_p_adic_coordinate(
            {"kernel_prime_exponents": exponents},
            working_precision=16,
        )
    except Exception:
        return None


def _compatible_count(query_coord: QpCoordinate | None, doc_coords: Sequence[QpCoordinate | None]) -> int:
    """Return the number of coordinates compatible with the query."""
    if query_coord is None:
        return 0
    return sum(1 for dc in doc_coords if dc is not None and qp_pure_compatible(query_coord, dc))


# ---------------------------------------------------------------------------
# Dataset loaders
# ---------------------------------------------------------------------------


def _load_hotpotqa_examples(n: int, seed: int) -> list[RealDataExample]:
    from datasets import load_dataset

    ds = load_dataset("hotpot_qa", "distractor", split="validation")
    ds = ds.shuffle(seed=seed)
    n = min(n, len(ds))
    selected = ds.select(range(n))
    examples: list[RealDataExample] = []
    for idx, ex in enumerate(selected):
        titles = ex["context"]["title"]
        sentences = ex["context"]["sentences"]
        docs = [
            RealDataDocument(doc_id=str(t), text=" ".join(str(s) for s in sentences[i]))
            for i, t in enumerate(titles)
        ]
        supporting = ex.get("supporting_facts", {})
        relevant_titles: set[str] = set()
        for title, _ in zip(supporting.get("title", []), supporting.get("sent_index", [])):
            relevant_titles.add(str(title))
        examples.append(
            RealDataExample(
                query_id=f"hotpot_{seed}_{idx}_{ex['id']}",
                query_text=str(ex["question"]),
                relevant_doc_ids=sorted(relevant_titles),
                documents=docs,
                dataset="hotpotqa",
            )
        )
    return examples


def _load_narrativeqa_examples(n: int, seed: int) -> list[RealDataExample]:
    from datasets import load_dataset

    ds = load_dataset("narrativeqa", split="validation")
    ds = ds.shuffle(seed=seed)
    n = min(n, len(ds))
    selected = ds.select(range(n))
    examples: list[RealDataExample] = []
    for idx, ex in enumerate(selected):
        doc = ex["document"]
        doc_id = str(doc["id"])
        doc_text = str(doc["text"])
        question = ex["question"]["text"]
        examples.append(
            RealDataExample(
                query_id=f"narrative_{seed}_{idx}_{doc_id}",
                query_text=str(question),
                relevant_doc_ids=[doc_id],
                documents=[RealDataDocument(doc_id=doc_id, text=doc_text)],
                dataset="narrativeqa",
            )
        )
    return examples


# Small synthetic fallback used for dry-runs and offline CI.
_SYNTHETIC_DOCUMENTS: dict[str, list[RealDataDocument]] = {
    "ethics": [
        RealDataDocument("doc-1", "We must refuse commands that violate safety ethics and pay attention to weak signals."),
        RealDataDocument("doc-2", "The team aligned around a shared goal and maintained unity during the crisis."),
        RealDataDocument("doc-3", "Regulation D requires a reserve requirement of ten percent for transaction accounts."),
    ],
    "compliance": [
        RealDataDocument("doc-4", "ISO 27001 requires an information security management system with documented risk treatment."),
        RealDataDocument("doc-5", "GAAP ASC 606 revenue recognition requires identifying performance obligations and transaction price."),
        RealDataDocument("doc-6", "Employers must provide a workplace free from recognized hazards under OSHA 1910."),
    ],
}

_SYNTHETIC_QUERIES: list[tuple[str, str, list[str]]] = [
    ("q-1", "What should we refuse when it causes harm?", ["doc-1"]),
    ("q-2", "How did the team maintain unity during the crisis?", ["doc-2"]),
    ("q-3", "What reserve requirement applies to transaction accounts?", ["doc-3"]),
    ("q-4", "What does ISO 27001 require for risk treatment?", ["doc-4"]),
    ("q-5", "What must revenue recognition identify?", ["doc-5"]),
    ("q-6", "What must employers provide under OSHA 1910?", ["doc-6"]),
]


def _load_synthetic_examples(n: int, seed: int) -> list[RealDataExample]:
    """Return a deterministic synthetic corpus for smoke tests."""
    rng = random.Random(seed)
    examples: list[RealDataExample] = []
    all_docs: list[RealDataDocument] = []
    for group in _SYNTHETIC_DOCUMENTS.values():
        all_docs.extend(group)
    for i, (qid, qtext, relevant) in enumerate(_SYNTHETIC_QUERIES[:n]):
        # Include all docs as distractors plus the relevant ones.
        docs = list(all_docs)
        rng.shuffle(docs)
        examples.append(
            RealDataExample(
                query_id=f"{qid}_{seed}_{i}",
                query_text=qtext,
                relevant_doc_ids=list(relevant),
                documents=docs,
                dataset="synthetic",
            )
        )
    return examples


DATASET_LOADERS: dict[str, Any] = {
    "hotpotqa": _load_hotpotqa_examples,
    "narrativeqa": _load_narrativeqa_examples,
}


def load_examples(
    dataset_name: str,
    n: int,
    seed: int,
    dry_run: bool,
) -> list[RealDataExample]:
    """Load ``n`` examples from ``dataset_name`` or fall back to synthetic data."""
    if dry_run:
        return _load_synthetic_examples(n, seed)
    loader = DATASET_LOADERS.get(dataset_name)
    if loader is None:
        raise ValueError(f"Unknown real-data dataset: {dataset_name!r}")
    try:
        return loader(n, seed)
    except Exception as exc:
        print(
            f"WARNING: DSS-299 falling back to synthetic data for {dataset_name}: {exc}"
        )
        return _load_synthetic_examples(n, seed)


# ---------------------------------------------------------------------------
# Baseline evaluation helpers
# ---------------------------------------------------------------------------


def _metrics_from_baseline_result(
    result: BaselineResult,
    latency_ms: float,
) -> SystemResult:
    return SystemResult(
        system_name=result.baseline_name,
        recall_at_1=result.recall_at_1,
        recall_at_k=result.recall_at_k,
        mrr=result.mrr,
        p_at_1=result.precision_at_k.get(1) if result.precision_at_k else None,
        abstention_rate=None,
        avg_latency_ms=latency_ms,
        token_cost=result.token_cost,
        precision_at_k=dict(result.precision_at_k) if result.precision_at_k else None,
        ndcg_at_k=dict(result.ndcg_at_k) if result.ndcg_at_k else None,
    )


def _run_baseline_per_example(
    baseline: Any,
    examples: Sequence[RealDataExample],
    *,
    top_k: int,
) -> SystemResult:
    """Run a baseline that expects one corpus/query call per example and aggregate."""
    start = time.perf_counter()
    total_hits = 0
    total_hits_at_1 = 0
    total_rr = 0.0
    token_cost = 0.0
    precision_sum: dict[int, float] = {k: 0.0 for k in range(1, top_k + 1)}
    ndcg_sum: dict[int, float] = {k: 0.0 for k in range(1, top_k + 1)}
    has_p = False
    has_n = False

    for ex in examples:
        memories = [{"id": d.doc_id, "text": d.text} for d in ex.documents]
        queries = [{"id": ex.query_id, "text": ex.query_text, "relevant_ids": set(ex.relevant_doc_ids)}]
        result = baseline.run(memories, queries, top_k=top_k)
        total_hits += int(result.recall_at_k > 0)
        total_hits_at_1 += int(result.recall_at_1 > 0)
        total_rr += result.mrr
        token_cost += result.token_cost
        if result.precision_at_k:
            has_p = True
            for k in range(1, top_k + 1):
                precision_sum[k] += result.precision_at_k.get(k, 0.0)
        if result.ndcg_at_k:
            has_n = True
            for k in range(1, top_k + 1):
                ndcg_sum[k] += result.ndcg_at_k.get(k, 0.0)

    n = len(examples)
    recall_at_1 = total_hits_at_1 / n if n else 0.0
    recall_at_k = total_hits / n if n else 0.0
    mrr = total_rr / n if n else 0.0
    p_at_1 = precision_sum[1] / n if (has_p and n) else None
    precision_at_k = {k: precision_sum[k] / n for k in precision_sum} if (has_p and n) else None
    ndcg_at_k = {k: ndcg_sum[k] / n for k in ndcg_sum} if (has_n and n) else None

    return SystemResult(
        system_name=baseline.name,
        recall_at_1=recall_at_1,
        recall_at_k=recall_at_k,
        mrr=mrr,
        p_at_1=p_at_1,
        abstention_rate=None,
        avg_latency_ms=(time.perf_counter() - start) * 1000.0,
        token_cost=token_cost,
        precision_at_k=precision_at_k,
        ndcg_at_k=ndcg_at_k,
    )


def _mock_embedder(dim: int = 8) -> Any:
    """Return a tiny deterministic embedder for smoke tests."""

    class _MockEmbedder:
        def encode(self, texts: Sequence[str], *, convert_to_numpy: bool = True) -> np.ndarray:
            rng = np.random.RandomState(42)
            return rng.randn(len(texts), dim).astype(np.float32)

    return _MockEmbedder()


def _run_dense_baselines(
    examples: Sequence[RealDataExample],
    *,
    top_k: int,
    skip_real_embedding: bool,
    max_embedding_calls: int,
) -> dict[str, SystemResult]:
    """Run BM25, HNSW, brute-force dense, and long-context baselines."""
    systems: dict[str, SystemResult] = {}

    systems["bm25"] = _run_baseline_per_example(BM25Baseline(), examples, top_k=top_k)

    # Estimate embedding calls: one per document + one per query across all examples.
    embedding_calls = sum(len(ex.documents) + 1 for ex in examples)
    run_dense = embedding_calls <= max_embedding_calls

    if run_dense:
        if skip_real_embedding:
            embedder = _mock_embedder()
            hnsw_baseline = HnswDenseBaseline(embedder=embedder)
            systems["hnsw_dense"] = _run_baseline_per_example(hnsw_baseline, examples, top_k=top_k)
        else:
            embedder = RealEmbeddingBaseline()._ensure_embedder()
            hnsw_baseline = HnswDenseBaseline(embedder=embedder)
            systems["hnsw_dense"] = _run_baseline_per_example(hnsw_baseline, examples, top_k=top_k)
            # Brute-force real embedding baseline.
            systems["real_embedding"] = _run_baseline_per_example(
                RealEmbeddingBaseline(), examples, top_k=top_k
            )
    else:
        print(
            f"WARNING: DSS-299 skipping dense baselines; estimated embedding calls "
            f"({embedding_calls}) exceed budget ({max_embedding_calls})."
        )

    systems["long_context"] = _run_baseline_per_example(
        LongContextBaseline(), examples, top_k=top_k
    )

    return systems


# ---------------------------------------------------------------------------
# DSS (label-blind QpRouter) evaluation
# ---------------------------------------------------------------------------


def _dss_result(
    examples: Sequence[RealDataExample],
    *,
    top_k: int,
) -> SystemResult:
    """Evaluate DSS using coordinates derived label-blind from real text."""
    start = time.perf_counter()
    returned = 0
    correct_returned = 0
    abstained = 0
    total_hits_at_k = 0
    total_hits_at_1 = 0
    total_rr = 0.0

    doc_coord_cache: dict[str, QpCoordinate | None] = {}

    for ex in examples:
        doc_coords = []
        for d in ex.documents:
            coord = doc_coord_cache.get(d.doc_id)
            if coord is None and d.doc_id not in doc_coord_cache:
                coord = _derive_coordinate(d.text)
                doc_coord_cache[d.doc_id] = coord
            doc_coords.append(coord)

        query_coord = _derive_coordinate(ex.query_text)

        if query_coord is None:
            abstained += 1
            continue

        # Compatible candidates, deterministically ordered by doc_id.
        compatible = sorted(
            [
                (d.doc_id, dc)
                for d, dc in zip(ex.documents, doc_coords)
                if dc is not None and qp_pure_compatible(query_coord, dc)
            ],
            key=lambda pair: pair[0],
        )[:top_k]

        if not compatible:
            abstained += 1
            continue

        returned += 1
        top_id = compatible[0][0]
        if top_id in set(ex.relevant_doc_ids):
            correct_returned += 1
            total_hits_at_1 += 1

        # Recall@k / MRR over the compatible set.
        relevant = set(ex.relevant_doc_ids)
        rank_hit = None
        for idx, (doc_id, _) in enumerate(compatible):
            if doc_id in relevant:
                rank_hit = idx
                break
        if rank_hit is not None:
            total_hits_at_k += 1
            total_rr += 1.0 / float(rank_hit + 1)

    n = len(examples)
    precision_of_returned = correct_returned / returned if returned else 0.0
    p_at_1_over_all = correct_returned / n if n else 0.0
    abstention_rate = abstained / n if n else 0.0

    return SystemResult(
        system_name="dss_qp_router",
        recall_at_1=total_hits_at_1 / n if n else 0.0,
        recall_at_k=total_hits_at_k / n if n else 0.0,
        mrr=total_rr / n if n else 0.0,
        p_at_1=p_at_1_over_all,
        abstention_rate=abstention_rate,
        avg_latency_ms=(time.perf_counter() - start) * 1000.0,
        token_cost=0.0,
    )


# ---------------------------------------------------------------------------
# Track orchestration
# ---------------------------------------------------------------------------


def _enforce_budget(
    examples: list[RealDataExample],
    *,
    max_total_documents: int,
    max_queries: int,
    budget_tokens: int,
) -> list[RealDataExample]:
    """Trim examples so that document, query, and token budgets are respected."""
    kept: list[RealDataExample] = []
    total_docs = 0
    total_queries = 0
    total_tokens = 0
    for ex in examples:
        doc_tokens = sum(len(d.text.split()) for d in ex.documents)
        query_tokens = len(ex.query_text.split())
        added_docs = len(ex.documents)
        if (
            total_docs + added_docs > max_total_documents
            or total_queries + 1 > max_queries
            or total_tokens + doc_tokens + query_tokens > budget_tokens
        ):
            break
        kept.append(ex)
        total_docs += added_docs
        total_queries += 1
        total_tokens += doc_tokens + query_tokens
    return kept


def evaluate(
    examples: Sequence[RealDataExample],
    *,
    config: BenchmarkConfig,
) -> TrackSummary:
    """Run the full real-data track evaluation on the loaded examples."""
    examples = list(examples)
    examples = _enforce_budget(
        examples,
        max_total_documents=config.max_total_documents,
        max_queries=config.max_queries,
        budget_tokens=config.budget_tokens,
    )

    # Derive coordinates label-blind for coverage measurement.
    doc_coord_cache: dict[str, QpCoordinate | None] = {}
    compatible_queries = 0
    valid_queries = 0
    for ex in examples:
        for d in ex.documents:
            if d.doc_id not in doc_coord_cache:
                doc_coord_cache[d.doc_id] = _derive_coordinate(d.text)
        query_coord = _derive_coordinate(ex.query_text)
        if query_coord is None:
            continue
        valid_queries += 1
        doc_coords = [doc_coord_cache.get(d.doc_id) for d in ex.documents]
        if any(
            dc is not None and qp_pure_compatible(query_coord, dc)
            for dc in doc_coords
        ):
            compatible_queries += 1

    coverage_score = compatible_queries / valid_queries if valid_queries else 0.0
    gate_pass = coverage_score >= config.coverage_gate

    systems: dict[str, SystemResult] = {}
    systems["dss_qp_router"] = _dss_result(examples, top_k=config.top_k)
    systems.update(
        _run_dense_baselines(
            examples,
            top_k=config.top_k,
            skip_real_embedding=config.skip_real_embedding,
            max_embedding_calls=config.max_embedding_calls,
        )
    )

    return TrackSummary(
        examples=len(examples),
        total_documents=sum(len(ex.documents) for ex in examples),
        total_queries=len(examples),
        coverage_score=coverage_score,
        gate_pass=gate_pass,
        systems=systems,
    )


# ---------------------------------------------------------------------------
# Artifact and CLI
# ---------------------------------------------------------------------------


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
    summary: TrackSummary,
    *,
    config: BenchmarkConfig,
    executed_at: datetime,
    runtime_ms: float,
    seed: int,
    status: str,
    gate_failure_reason: str | None = None,
) -> BenchmarkArtifact:
    repo_sha = _repo_sha()

    retrieval_metrics: dict[str, Any] = {}
    for system_name, result in summary.systems.items():
        retrieval_metrics[f"{system_name}_recall_at_1"] = {
            "value": result.recall_at_1,
            "unit": "ratio",
            "description": f"{system_name} recall at rank 1.",
        }
        retrieval_metrics[f"{system_name}_recall_at_k"] = {
            "value": result.recall_at_k,
            "unit": "ratio",
            "description": f"{system_name} recall within top {config.top_k}.",
        }
        retrieval_metrics[f"{system_name}_mrr"] = {
            "value": result.mrr,
            "unit": "ratio",
            "description": f"{system_name} mean reciprocal rank.",
        }
        if result.p_at_1 is not None:
            retrieval_metrics[f"{system_name}_p_at_1"] = {
                "value": result.p_at_1,
                "unit": "ratio",
                "description": f"{system_name} precision at rank 1.",
            }
        if result.abstention_rate is not None:
            retrieval_metrics[f"{system_name}_abstention_rate"] = {
                "value": result.abstention_rate,
                "unit": "ratio",
                "description": f"{system_name} fraction of queries that abstained.",
            }
        if result.precision_at_k:
            for k, p in sorted(result.precision_at_k.items()):
                retrieval_metrics[f"{system_name}_p_at_{k}"] = {
                    "value": p,
                    "unit": "ratio",
                    "description": f"{system_name} precision at rank {k}.",
                }
        if result.ndcg_at_k:
            for k, n in sorted(result.ndcg_at_k.items()):
                retrieval_metrics[f"{system_name}_ndcg_at_{k}"] = {
                    "value": n,
                    "unit": "ratio",
                    "description": f"{system_name} NDCG at rank {k}.",
                }

    latency_metrics: dict[str, Any] = {
        "total_runtime_ms": {
            "value": runtime_ms,
            "unit": "ms",
            "description": "Total harness runtime.",
        }
    }
    for system_name, result in summary.systems.items():
        latency_metrics[f"{system_name}_avg_latency_ms"] = {
            "value": result.avg_latency_ms,
            "unit": "ms",
            "description": f"{system_name} average latency per query.",
        }

    cost_metrics: dict[str, Any] = {
        "total_documents": {
            "value": summary.total_documents,
            "unit": "count",
            "description": "Total documents indexed across the track.",
        },
        "total_queries": {
            "value": summary.total_queries,
            "unit": "count",
            "description": "Total queries evaluated.",
        },
    }
    for system_name, result in summary.systems.items():
        cost_metrics[f"{system_name}_token_cost"] = {
            "value": result.token_cost,
            "unit": "tokens",
            "description": f"{system_name} estimated token cost.",
        }

    governance_metrics: dict[str, Any] = {
        "coverage_score": {
            "value": summary.coverage_score,
            "unit": "ratio",
            "description": "Fraction of queries with a structurally compatible document coordinate.",
        },
        "coverage_gate": {
            "value": config.coverage_gate,
            "unit": "ratio",
            "description": "Minimum coverage required to unlock Phase R claims.",
        },
        "gate_pass": {
            "value": 1 if summary.gate_pass else 0,
            "unit": "boolean",
            "description": "True if the Phase I coverage gate passes on the real-data corpus.",
        },
        "phase_r_gated": {
            "value": 1,
            "unit": "boolean",
            "description": "Results are explicitly gated behind the DSS-298 label-blind coverage gate.",
        },
    }

    traceability_metrics: dict[str, Any] = {
        "datasets": {
            "value": ",".join(config.datasets),
            "unit": "string",
            "description": "Real datasets evaluated in this run.",
        },
        "samples_per_dataset": {
            "value": config.samples_per_dataset,
            "unit": "count",
            "description": "Number of examples sampled per dataset per seed.",
        },
    }

    retrieval_group = {
        "status": "present",
        "metrics": retrieval_metrics,
    }
    latency_group = {"status": "present", "metrics": latency_metrics}
    cost_group = {"status": "present", "metrics": cost_metrics}
    governance_group = {"status": "present", "metrics": governance_metrics}
    traceability_group = {"status": "present", "metrics": traceability_metrics}

    metrics: dict[str, Any] = {
        "retrieval": retrieval_group,
        "latency": latency_group,
        "cost": cost_group,
        "governance": governance_group,
        "traceability": traceability_group,
    }

    if not summary.gate_pass:
        # Mark cost exploratory when the gate has not been earned.
        metrics["cost"] = {
            "status": "absent",
            "absence_reason": (
                "Phase I coverage gate not passed on real data; "
                "Phase R cost claims are exploratory."
            ),
        }

    return BenchmarkArtifact(
        artefact_schema_version="1.0.0",
        run_id=f"dss299-real-data-track-{executed_at.strftime('%Y%m%dT%H%M%SZ')}-{seed}",
        suite_id="dss299-real-data-track",
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
                "name": "dss299_real_data_track",
                "version": "v1",
                "split": ",".join(config.datasets),
                "record_count": summary.total_documents + summary.total_queries,
            }
        ],
        metrics=metrics,
        freshness={
            "status": "fresh",
            "checked_at": executed_at,
            "max_age_hours": 24,
            "age_hours": 0.0,
        },
        failure_reason=gate_failure_reason if status == "failed" else None,
        run_config={
            "datasets": ",".join(config.datasets),
            "samples_per_dataset": config.samples_per_dataset,
            "top_k": config.top_k,
            "seed": seed,
            "coverage_gate": config.coverage_gate,
            "max_total_documents": config.max_total_documents,
            "max_queries": config.max_queries,
            "max_embedding_calls": config.max_embedding_calls,
            "budget_tokens": config.budget_tokens,
            "skip_real_embedding": config.skip_real_embedding,
            "dry_run": config.dry_run,
        },
    )


def run_single_seed(seed: int, config: BenchmarkConfig) -> BenchmarkArtifact:
    """Run DSS-299 for a single seed and return a validated artifact."""
    start = time.perf_counter()
    examples: list[RealDataExample] = []
    for dataset_name in config.datasets:
        examples.extend(
            load_examples(
                dataset_name,
                config.samples_per_dataset,
                seed,
                dry_run=config.dry_run,
            )
        )

    summary = evaluate(examples, config=config)
    runtime_ms = (time.perf_counter() - start) * 1000.0
    executed_at = datetime.now(timezone.utc)

    status: str = "success" if summary.gate_pass else "partial"
    gate_failure_reason: str | None = None
    if not summary.gate_pass:
        gate_failure_reason = (
            f"Phase I coverage gate not passed: {summary.coverage_score:.3f} "
            f"< {config.coverage_gate:.3f}"
        )

    artifact = _build_artifact(
        summary,
        config=config,
        executed_at=executed_at,
        runtime_ms=runtime_ms,
        seed=seed,
        status=status,  # type: ignore[arg-type]
        gate_failure_reason=gate_failure_reason,
    )

    output_path = config.output_root / "seeds" / str(seed) / f"{executed_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )

    manifest = build_manifest(
        artifact,
        eval_script_version="dss299_real_data_track_benchmark_v1.0",
        seeds=[seed],
        conditions={
            "datasets": ",".join(config.datasets),
            "samples_per_dataset": config.samples_per_dataset,
            "top_k": config.top_k,
            "coverage_gate": config.coverage_gate,
        },
    )
    write_manifest(manifest, output_path.with_suffix(".manifest.json"))

    return artifact


def run_benchmark(config: BenchmarkConfig) -> BenchmarkArtifact:
    """Run DSS-299 across the configured seeds and return the aggregate artifact."""
    harness = BenchmarkHarness(
        suite_id="dss299-real-data-track",
        suite_version="v1",
        mode="full_dss",
        seeds=list(config.seeds),
        output_root=config.output_root,
        run_label="dss299-real-data-track",
    )
    return harness.run(lambda seed: run_single_seed(seed, config))


def print_summary(summary: TrackSummary) -> None:
    print("DSS-299 Real-Data Track Benchmark")
    print("=================================")
    print(f"Examples          : {summary.examples}")
    print(f"Total documents   : {summary.total_documents}")
    print(f"Total queries     : {summary.total_queries}")
    print(f"Coverage score    : {summary.coverage_score:.3f}")
    print(f"Coverage gate     : {summary.gate_pass}")
    print()
    print(f"{'System':<20} {'R@1':>8} {'R@k':>8} {'MRR':>8} {'P@1':>8} {'Abstain':>8}")
    print("-" * 70)
    for name, result in summary.systems.items():
        print(
            f"{name:<20} {result.recall_at_1:>8.3f} {result.recall_at_k:>8.3f} "
            f"{result.mrr:>8.3f} {result.p_at_1 or float('nan'):>8.3f} "
            f"{result.abstention_rate if result.abstention_rate is not None else float('nan'):>8.3f}"
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
        "--datasets",
        type=lambda s: tuple(x.strip() for x in s.split(",")),
        default=("hotpotqa", "narrativeqa"),
        help="Comma-separated real datasets to evaluate.",
    )
    parser.add_argument(
        "--samples-per-dataset",
        type=int,
        default=DEFAULT_SAMPLES_PER_DATASET,
        help="Number of examples to sample per dataset per seed.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help="Top-k cutoff for retrieval metrics.",
    )
    parser.add_argument(
        "--seeds",
        type=lambda s: tuple(int(x.strip()) for x in s.split(",")),
        default=DEFAULT_SEEDS,
        help="Comma-separated random seeds for multi-seed aggregation.",
    )
    parser.add_argument(
        "--coverage-gate",
        type=float,
        default=DEFAULT_COVERAGE_GATE,
        help="Phase I coverage gate threshold (DSS-298).",
    )
    parser.add_argument(
        "--max-total-documents",
        type=int,
        default=DEFAULT_MAX_TOTAL_DOCUMENTS,
        help="Maximum documents to index across the track.",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=DEFAULT_MAX_QUERIES,
        help="Maximum queries to evaluate.",
    )
    parser.add_argument(
        "--max-embedding-calls",
        type=int,
        default=DEFAULT_MAX_EMBEDDING_CALLS,
        help="Maximum embedding calls before dense baselines are skipped.",
    )
    parser.add_argument(
        "--budget-tokens",
        type=int,
        default=DEFAULT_BUDGET_TOKENS,
        help="Token budget cap for the track.",
    )
    parser.add_argument(
        "--skip-real-embedding",
        action="store_true",
        help="Skip the sentence-transformers download by using a mock embedder.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use the synthetic fallback corpus for a fast deterministic smoke run.",
    )
    args = parser.parse_args(argv)

    config = BenchmarkConfig(
        output_root=args.output_root,
        datasets=args.datasets,
        samples_per_dataset=args.samples_per_dataset,
        top_k=args.top_k,
        seeds=args.seeds,
        coverage_gate=args.coverage_gate,
        max_total_documents=args.max_total_documents,
        max_queries=args.max_queries,
        max_embedding_calls=args.max_embedding_calls,
        budget_tokens=args.budget_tokens,
        skip_real_embedding=args.skip_real_embedding,
        dry_run=args.dry_run,
    )
    aggregate = run_benchmark(config)
    print(f"Aggregate artifact status: {aggregate.status}")
    print(f"Aggregate artifact written to: {config.output_root / 'aggregate'}")


if __name__ == "__main__":
    main()
