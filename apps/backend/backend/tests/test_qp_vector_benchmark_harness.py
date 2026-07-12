"""Tests for the Qp vs vector-RAG benchmark harness (DS-REVIEW-193 P2-05)."""

from __future__ import annotations

import json

import pytest

from backend.benchmarks.artifact_schema import validate_benchmark_artifact
from backend.benchmarks.retrieval_qp_vs_rag import (
    DEFAULT_CORPUS_PATH,
    BenchmarkConfig,
    QpRouter,
    VectorRAGBaseline,
    load_corpus,
    run_benchmark,
)


def test_load_corpus_derives_coordinates() -> None:
    memories, queries = load_corpus(DEFAULT_CORPUS_PATH)
    assert len(memories) > 0
    assert len(queries) > 0
    for memory in memories:
        assert memory.coordinate is not None
        assert memory.coordinate.rational_representative is not None


def test_qp_router_ranks_exact_matches_first() -> None:
    memories, queries = load_corpus(DEFAULT_CORPUS_PATH)
    router = QpRouter(memories, use_filters=True)
    vector = VectorRAGBaseline(memories)

    exact_query = next(q for q in queries if q.task == "exact_continuation")
    ranked = [mid for mid, _ in router.rank(exact_query, top_k=10)]
    assert ranked[0] in exact_query.relevant_ids


def test_dual_overlay_filter_changes_ablation_ranking() -> None:
    memories, queries = load_corpus(DEFAULT_CORPUS_PATH)
    ablation_query = next(q for q in queries if q.task == "dual_overlay_ablation")

    router_on = QpRouter(memories, use_filters=True)
    router_off = QpRouter(memories, use_filters=False)

    on_ranked = [mid for mid, _ in router_on.rank(ablation_query, top_k=10)]
    off_ranked = [mid for mid, _ in router_off.rank(ablation_query, top_k=10)]

    # With filters on, the incompatible dual-state candidate is excluded.
    assert len(on_ranked) < len(off_ranked)
    assert on_ranked[0] in ablation_query.relevant_ids


def test_run_benchmark_produces_valid_artifact(tmp_path) -> None:
    config = BenchmarkConfig(
        corpus_path=DEFAULT_CORPUS_PATH,
        output_root=tmp_path / "output",
        top_k=5,
        permutations=500,
        use_qp_filters=True,
        seed=42,
    )
    summary, per_query_results, output_path = run_benchmark(config)

    assert output_path.exists()
    payload = json.loads(output_path.read_text())
    artifact = validate_benchmark_artifact(payload)
    assert artifact.suite_id == "qp-vs-rag"
    assert artifact.metrics["retrieval"].status == "present"
    assert summary.queries == len(per_query_results)


def test_run_benchmark_ablation_produces_different_results(tmp_path) -> None:
    base_config = BenchmarkConfig(
        corpus_path=DEFAULT_CORPUS_PATH,
        output_root=tmp_path / "output",
        top_k=5,
        permutations=500,
        use_qp_filters=True,
        seed=42,
    )
    ablation_config = BenchmarkConfig(
        corpus_path=DEFAULT_CORPUS_PATH,
        output_root=tmp_path / "output",
        top_k=5,
        permutations=500,
        use_qp_filters=False,
        seed=42,
    )
    base_summary, _, _ = run_benchmark(base_config)
    ablation_summary, _, _ = run_benchmark(ablation_config)

    assert base_summary.ablation_label == "filters_on"
    assert ablation_summary.ablation_label == "filters_off"
    assert base_summary.mrr_qp != pytest.approx(ablation_summary.mrr_qp)
