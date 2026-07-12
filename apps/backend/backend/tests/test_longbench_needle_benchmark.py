"""Tests for the LongBench-style needle retrieval benchmark (DS-REVIEW-193 follow-up)."""

from __future__ import annotations

import json

import pytest

from backend.benchmarks.artifact_schema import validate_benchmark_artifact
from backend.benchmarks.longbench_needle_benchmark import (
    BenchmarkConfig,
    QpRouter,
    VectorRAGBaseline,
    evaluate,
    generate_corpus,
    run_benchmark,
)


def test_generate_corpus_builds_coordinates() -> None:
    memories, queries = generate_corpus((4, 16), seed=42)
    assert memories
    assert queries
    for memory in memories:
        assert memory.coordinate is not None
        assert memory.coordinate.rational_representative is not None
    for query in queries:
        assert query.coordinate is not None


def test_qp_router_ranks_needle_first_across_lengths() -> None:
    lengths = (4, 16, 64)
    memories, queries = generate_corpus(lengths, seed=42)
    memories_by_length: dict[int, list] = {}
    for memory in memories:
        memories_by_length.setdefault(memory.length, []).append(memory)

    for query in queries:
        router = QpRouter(memories_by_length[query.length])
        ranked = [mid for mid, _ in router.rank(query, top_k=5)]
        assert ranked[0] == query.needle_id, f"failed at length {query.length}"


def test_vector_recall_degrades_as_haystack_grows() -> None:
    lengths = (4, 16, 64, 256)
    memories, queries = generate_corpus(lengths, seed=42)
    summary, _ = evaluate(memories, queries, top_k=5, permutations=500, seed=42)

    # Qp should keep the needle at rank 1 regardless of haystack size.
    assert summary.qp_recall_at_1 == 1.0
    # Vector-RAG must degrade: the smallest haystack may succeed, the largest must fail.
    assert summary.vector_recall_at_1 < summary.qp_recall_at_1
    assert summary.vector_recall_at_k <= summary.qp_recall_at_k


def test_run_benchmark_produces_valid_artifact(tmp_path) -> None:
    config = BenchmarkConfig(
        output_root=tmp_path / "output",
        lengths=(8, 32),
        top_k=5,
        permutations=500,
        seed=42,
    )
    summary, per_query, output_path = run_benchmark(config)

    assert output_path.exists()
    payload = json.loads(output_path.read_text())
    artifact = validate_benchmark_artifact(payload)
    assert artifact.suite_id == "longbench-needle"
    assert artifact.metrics["retrieval"].status == "present"
    assert summary.qp_recall_at_1 > summary.vector_recall_at_1


def test_longbench_advantage_is_significant(tmp_path) -> None:
    config = BenchmarkConfig(
        output_root=tmp_path / "output",
        lengths=(4, 8, 16, 32, 64, 128, 256),
        top_k=5,
        permutations=2_000,
        seed=42,
    )
    summary, _, _ = run_benchmark(config)
    assert summary.p_value is not None
    assert summary.p_value < 0.05
