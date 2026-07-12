"""Tests for the architecture-aligned Qp retrieval benchmark (DS-REVIEW-193 P2-06 follow-up)."""

from __future__ import annotations

import json

import pytest

from backend.benchmarks.artifact_schema import validate_benchmark_artifact
from backend.benchmarks.retrieval_architecture_benchmark import (
    DEFAULT_CORPUS_PATH,
    BenchmarkConfig,
    QpRouter,
    VectorRAGBaseline,
    load_corpus,
    run_benchmark,
)


def test_load_corpus_builds_coordinates() -> None:
    memories, queries = load_corpus(DEFAULT_CORPUS_PATH)
    assert memories
    assert queries
    for memory in memories:
        assert memory.coordinate is not None
        assert memory.coordinate.rational_representative is not None


def test_qp_router_prefers_valid_candidates() -> None:
    memories, queries = load_corpus(DEFAULT_CORPUS_PATH)
    router = QpRouter(memories)
    vector = VectorRAGBaseline(memories)

    # Pick the dual-sync query, which has an invalid-dual distractor.
    query = next(q for q in queries if q.task_type == "dual_sync")
    qp_ranked = [mid for mid, _ in router.rank(query, top_k=5)]
    vector_ranked = [mid for mid, _ in vector.rank(query.text, top_k=5)]

    assert qp_ranked[0].endswith(":valid_sync")
    # Vector may rank the semantic distractor or broken-dual candidate first.
    assert not vector_ranked[0].endswith(":valid_sync") or len(vector_ranked) > 1


def test_qp_incoherent_rate_is_lower_than_vector() -> None:
    memories, queries = load_corpus(DEFAULT_CORPUS_PATH)
    from backend.benchmarks.retrieval_architecture_benchmark import evaluate

    summary, _ = evaluate(memories, queries, top_k=5, permutations=1000, seed=42)
    assert summary.qp_incoherent_rate < summary.vector_incoherent_rate


def test_run_benchmark_produces_valid_artifact(tmp_path) -> None:
    config = BenchmarkConfig(
        corpus_path=DEFAULT_CORPUS_PATH,
        output_root=tmp_path / "output",
        top_k=5,
        permutations=1000,
        seed=42,
    )
    summary, per_query, output_path = run_benchmark(config)

    assert output_path.exists()
    payload = json.loads(output_path.read_text())
    artifact = validate_benchmark_artifact(payload)
    assert artifact.suite_id == "qp-architecture"
    assert artifact.metrics["retrieval"].status == "present"
    assert summary.qp_precision_at_5 > summary.vector_precision_at_5


def test_architecture_advantage_is_significant(tmp_path) -> None:
    config = BenchmarkConfig(
        corpus_path=DEFAULT_CORPUS_PATH,
        output_root=tmp_path / "output",
        top_k=5,
        permutations=2000,
        seed=42,
    )
    summary, _, _ = run_benchmark(config)
    assert summary.p_value is not None
    assert summary.p_value < 0.05
