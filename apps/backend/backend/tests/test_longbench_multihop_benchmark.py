"""Tests for the LongBench-style multi-hop QA benchmark (DS-REVIEW-193 follow-up)."""

from __future__ import annotations

import json

import pytest

from backend.benchmarks.artifact_schema import validate_benchmark_artifact
from backend.benchmarks.longbench_multihop_benchmark import (
    BenchmarkConfig,
    QpRouter,
    VectorRAGBaseline,
    evaluate,
    generate_corpus,
    run_benchmark,
)


def test_generate_corpus_builds_coordinates() -> None:
    memories, queries = generate_corpus(5, seed=42)
    assert memories
    assert queries
    for memory in memories:
        assert memory.coordinate is not None
        assert memory.coordinate.rational_representative is not None
    for query in queries:
        assert query.coordinate is not None
        assert query.required_ids


def test_qp_router_retrieves_full_chain() -> None:
    memories, queries = generate_corpus(5, seed=42)
    router = QpRouter(memories)
    for query in queries:
        ranked = {mid for mid, _ in router.rank(query, top_k=5)}
        missing = query.required_ids - ranked
        assert not missing, f"{query.query_id} missing {missing}"


def test_vector_chain_recall_is_lower_than_qp() -> None:
    memories, queries = generate_corpus(5, seed=42)
    summary, _ = evaluate(memories, queries, top_k=5, permutations=500, seed=42)
    assert summary.qp_chain_recall == 1.0
    assert summary.vector_chain_recall < summary.qp_chain_recall
    assert summary.qp_full_chain_rate > summary.vector_full_chain_rate


def test_run_benchmark_produces_valid_artifact(tmp_path) -> None:
    config = BenchmarkConfig(
        output_root=tmp_path / "output",
        chain_count=5,
        chain_length=3,
        top_k=5,
        permutations=500,
        seed=42,
    )
    summary, per_query, output_path = run_benchmark(config)

    assert output_path.exists()
    payload = json.loads(output_path.read_text())
    artifact = validate_benchmark_artifact(payload)
    assert artifact.suite_id == "longbench-multihop"
    assert artifact.metrics["retrieval"].status == "present"
    assert summary.qp_full_chain_rate > summary.vector_full_chain_rate


def test_multihop_advantage_is_significant(tmp_path) -> None:
    config = BenchmarkConfig(
        output_root=tmp_path / "output",
        chain_count=9,
        chain_length=3,
        top_k=5,
        permutations=2_000,
        seed=42,
    )
    summary, _, _ = run_benchmark(config)
    assert summary.p_value is not None
    assert summary.p_value < 0.05
