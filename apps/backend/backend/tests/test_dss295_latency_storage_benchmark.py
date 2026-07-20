"""Tests for DSS-295 latency and storage cost benchmark."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.benchmarks.artifact_schema import validate_benchmark_artifact
from backend.benchmarks.comparison_baselines import BaselineResult
from backend.benchmarks.dss295_latency_storage_benchmark import (
    BenchmarkConfig,
    _measure_baseline_latency,
    _measure_bm25_storage,
    _measure_bow_storage,
    _measure_dss_storage,
    _measure_metadata_filter_storage,
    _measure_minilm_storage,
    _percentile,
    evaluate,
    run_benchmark,
    run_single_seed,
)
from backend.benchmarks.longbench_needle_benchmark import (
    NeedleMemory,
    NeedleQuery,
    _make_coordinate,
    generate_corpus,
)


def _tiny_corpus() -> tuple[list[NeedleMemory], list[NeedleQuery]]:
    coord = _make_coordinate(
        kernel_node="Eq2",
        valuation_offset=3,
        circulation_pass=3,
        hysteresis_depth=0.3,
        dual_valid=True,
    )
    memories = [
        NeedleMemory(
            memory_id="needle",
            text="Meeting time is 9:00 and the smallest prime number discussed was 2.",
            coordinate=coord,
            is_needle=True,
            length=2,
        ),
        NeedleMemory(
            memory_id="distractor",
            text="The weather was sunny and the team had lunch at noon.",
            coordinate=_make_coordinate(
                kernel_node="Eq4",
                valuation_offset=5,
                circulation_pass=2,
                hysteresis_depth=0.4,
                dual_valid=True,
            ),
            is_needle=False,
            length=2,
        ),
    ]
    queries = [
        NeedleQuery(
            query_id="q1",
            text="What was the meeting time and the smallest prime number discussed?",
            coordinate=coord,
            needle_id="needle",
            length=2,
        )
    ]
    return memories, queries


def _mock_real_embedding_baseline() -> MagicMock:
    """Return a mock RealEmbeddingBaseline that avoids downloading weights."""
    import numpy as np

    mock_baseline = MagicMock()
    mock_baseline.run.return_value = BaselineResult(
        baseline_name="real_embedding",
        recall_at_1=1.0,
        recall_at_k=1.0,
        mrr=1.0,
        avg_latency_ms=1.0,
        token_cost=10.0,
        prompt_tokens=5.0,
        completion_tokens=5.0,
    )
    mock_baseline.name = "real_embedding"

    mock_embedder = MagicMock()

    def _encode(texts, *, convert_to_numpy=True):
        rng = np.random.RandomState(42)
        return rng.randn(len(texts), 8).astype(np.float32)

    mock_embedder.encode.side_effect = _encode
    mock_baseline._ensure_embedder.return_value = mock_embedder
    return mock_baseline


def test_percentile_handles_empty() -> None:
    assert _percentile([], 0.5) == 0.0


def test_percentile_monotonic() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(values, 0.5) == pytest.approx(3.0)
    assert _percentile(values, 0.95) >= _percentile(values, 0.5)


def test_generate_corpus_produces_expected_event_count() -> None:
    memories, queries = generate_corpus((4,), seed=193)
    # One needle + length distractors.
    assert len(memories) == 5
    assert len(queries) == 1


def test_measure_dss_storage_positive() -> None:
    memories, _ = _tiny_corpus()
    assert _measure_dss_storage(memories) > 0


def test_measure_minilm_storage_matches_formula() -> None:
    assert _measure_minilm_storage(10) == 10 * 384 * 4


def test_measure_bm25_storage_positive() -> None:
    memories, _ = _tiny_corpus()
    assert _measure_bm25_storage(memories) > 0


def test_measure_metadata_filter_storage_positive() -> None:
    memories, _ = _tiny_corpus()
    assert _measure_metadata_filter_storage(memories) > 0


def test_measure_bow_storage_positive() -> None:
    memories, _ = _tiny_corpus()
    assert _measure_bow_storage(memories) > 0


def test_measure_baseline_latency_returns_nonnegative() -> None:
    from backend.benchmarks.comparison_baselines import BM25Baseline

    memories, queries = _tiny_corpus()
    memory_dicts = [{"id": m.memory_id, "text": m.text, "coordinate": m.coordinate} for m in memories]
    query_dicts = [
        {"id": q.query_id, "text": q.text, "relevant_ids": {q.needle_id}, "coordinate": q.coordinate}
        for q in queries
    ]
    baseline = BM25Baseline()
    latencies = _measure_baseline_latency(
        baseline,
        memory_dicts,
        query_dicts,
        iterations=3,
        warmup=1,
        top_k=2,
    )
    assert len(latencies) == 3
    assert all(l >= 0.0 for l in latencies)


def test_evaluate_produces_all_systems() -> None:
    mock_baseline = _mock_real_embedding_baseline()
    with patch("backend.benchmarks.dss295_latency_storage_benchmark.RealEmbeddingBaseline", return_value=mock_baseline):
        config = BenchmarkConfig(
            output_root=None,  # type: ignore[arg-type]
            corpus_sizes=(99,),
            top_k=2,
            query_iterations=3,
            warmup_iterations=1,
            seeds=(193,),
            measure_100k=True,
            max_measured_events=200,
        )
        summary = evaluate(config, seed=193)
        assert len(summary.sizes) == 1
        size_result = summary.sizes[0]
        assert size_result.measured is True
        assert set(size_result.systems.keys()) == {
            "dss_qp_router",
            "real_embedding",
            "hnsw_dense",
            "bm25",
            "metadata_filter",
            "bow_stand_in",
        }
        for result in size_result.systems.values():
            assert result.p50_latency_ms >= 0.0
            assert result.p95_latency_ms >= result.p50_latency_ms
            assert result.bytes_per_event > 0.0


def test_evaluate_extrapolates_large_size() -> None:
    mock_baseline = _mock_real_embedding_baseline()
    with patch("backend.benchmarks.dss295_latency_storage_benchmark.RealEmbeddingBaseline", return_value=mock_baseline):
        config = BenchmarkConfig(
            output_root=None,  # type: ignore[arg-type]
            corpus_sizes=(99, 199),
            top_k=2,
            query_iterations=3,
            warmup_iterations=1,
            seeds=(193,),
            measure_100k=False,
            max_measured_events=100,
        )
        summary = evaluate(config, seed=193)
        assert len(summary.sizes) == 2
        measured = summary.sizes[0]
        extrapolated = summary.sizes[1]
        assert measured.measured is True
        assert extrapolated.measured is False
        assert "Extrapolated" in extrapolated.extrapolation_note


def test_single_seed_artifact_validates_without_real_model(tmp_path) -> None:
    mock_baseline = _mock_real_embedding_baseline()
    with patch("backend.benchmarks.dss295_latency_storage_benchmark.RealEmbeddingBaseline", return_value=mock_baseline):
        config = BenchmarkConfig(
            output_root=tmp_path,
            corpus_sizes=(99,),
            top_k=2,
            query_iterations=3,
            warmup_iterations=1,
            seeds=(193,),
            measure_100k=True,
            max_measured_events=200,
        )
        artifact = run_single_seed(193, config)
        payload = artifact.model_dump(mode="json")
        validated = validate_benchmark_artifact(payload)
        assert validated.status == "success"
        assert validated.metrics["latency"].status == "present"
        assert validated.metrics["cost"].status == "present"
        assert validated.metrics["governance"].status == "present"
        assert "events_100_dss_qp_router_p50_ms" in validated.metrics["latency"].metrics


def test_multi_seed_benchmark_produces_aggregate(tmp_path) -> None:
    mock_baseline = _mock_real_embedding_baseline()
    with patch("backend.benchmarks.dss295_latency_storage_benchmark.RealEmbeddingBaseline", return_value=mock_baseline):
        config = BenchmarkConfig(
            output_root=tmp_path,
            corpus_sizes=(99,),
            top_k=2,
            query_iterations=3,
            warmup_iterations=1,
            seeds=(193, 42),
            measure_100k=True,
            max_measured_events=200,
        )
        aggregate = run_benchmark(config)
        assert aggregate.status == "success"
        assert aggregate.run_config.get("aggregate") is True
        assert aggregate.run_config.get("seed_count") == 2
        assert aggregate.metrics["latency"].metrics["events_100_dss_qp_router_p50_ms"].statistics is not None
