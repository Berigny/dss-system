"""Tests for DSS-294 BM25 baseline + ranking metrics."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.benchmarks.artifact_schema import validate_benchmark_artifact
from backend.benchmarks.comparison_baselines import BM25Baseline, BaselineResult
from backend.benchmarks.dss294_bm25_baseline import (
    BenchmarkConfig,
    _dss_result,
    _normalize_for_baseline,
    _ranking_result_from_baseline,
    evaluate,
    run_benchmark,
    run_single_seed,
)
from backend.benchmarks.longbench_needle_benchmark import (
    NeedleMemory,
    NeedleQuery,
    _make_coordinate,
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


def test_bm25_baseline_computes_precision_and_ndcg() -> None:
    baseline = BM25Baseline()
    memories, queries = _tiny_corpus()
    memory_dicts, query_dicts = _normalize_for_baseline(memories, queries)
    result = baseline.run(memory_dicts, query_dicts, top_k=2)
    assert result.precision_at_k is not None
    assert result.ndcg_at_k is not None
    assert 1 in result.precision_at_k
    assert result.recall_at_1 == pytest.approx(1.0)


def test_dss_result_documents_one_return_or_abstention() -> None:
    memories, queries = _tiny_corpus()
    result = _dss_result(memories, queries, top_k=2)
    assert result.system_name == "dss_qp_router"
    assert result.p_at_k == {1: pytest.approx(1.0)}
    assert result.ndcg_at_k == {}
    assert result.abstention_rate == pytest.approx(0.0)


def test_ranking_result_from_bm25_baseline() -> None:
    memories, queries = _tiny_corpus()
    result = _ranking_result_from_baseline(
        BM25Baseline(), memories, queries, top_k=2
    )
    assert result.system_name == "bm25"
    assert result.p_at_k is not None
    assert result.ndcg_at_k is not None
    assert result.recall_at_1 == pytest.approx(1.0)


def test_evaluate_includes_all_systems() -> None:
    mock_baseline = _mock_real_embedding_baseline()
    with patch(
        "backend.benchmarks.dss294_bm25_baseline.RealEmbeddingBaseline",
        return_value=mock_baseline,
    ):
        memories, queries = _tiny_corpus()
        summary = evaluate(memories, queries, top_k=2)
        assert set(summary.systems.keys()) == {
            "dss_qp_router",
            "real_embedding",
            "hnsw_dense",
            "bm25",
            "metadata_filter",
            "bow_stand_in",
        }


def _mock_real_embedding_baseline(memory_count: int = 2) -> MagicMock:
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
        precision_at_k={1: 1.0, 2: 0.5},
        ndcg_at_k={1: 1.0, 2: 1.0},
    )
    mock_embedder = MagicMock()

    def _encode(texts, *, convert_to_numpy=True):
        # Return deterministic random embeddings sized to the input batch.
        rng = np.random.RandomState(42)
        return rng.randn(len(texts), 8).astype(np.float32)

    mock_embedder.encode.side_effect = _encode
    mock_baseline._ensure_embedder.return_value = mock_embedder
    mock_baseline.name = "real_embedding"
    return mock_baseline


def test_single_seed_artifact_validates_without_real_model(tmp_path) -> None:
    """Mock the real embedding baseline so tests do not download weights."""
    mock_baseline = _mock_real_embedding_baseline()
    with patch(
        "backend.benchmarks.dss294_bm25_baseline.RealEmbeddingBaseline",
        return_value=mock_baseline,
    ):
        config = BenchmarkConfig(
            output_root=tmp_path,
            lengths=(2,),
            top_k=2,
            seeds=(193,),
        )
        artifact = run_single_seed(193, config)
        payload = artifact.model_dump(mode="json")
        validated = validate_benchmark_artifact(payload)
        assert validated.status == "success"
        assert validated.metrics["retrieval"].status == "present"
        assert "dss_qp_router_p_at_1" in validated.metrics["retrieval"].metrics
        assert "bm25_p_at_1" in validated.metrics["retrieval"].metrics


def test_multi_seed_benchmark_produces_aggregate(tmp_path) -> None:
    mock_baseline = _mock_real_embedding_baseline()
    with patch(
        "backend.benchmarks.dss294_bm25_baseline.RealEmbeddingBaseline",
        return_value=mock_baseline,
    ):
        config = BenchmarkConfig(
            output_root=tmp_path,
            lengths=(2, 4),
            top_k=2,
            seeds=(193, 42),
        )
        aggregate = run_benchmark(config)
        assert aggregate.status == "success"
        assert aggregate.run_config.get("aggregate") is True
        assert aggregate.run_config.get("seed_count") == 2
        assert aggregate.metrics["retrieval"].metrics["bm25_p_at_1"].statistics is not None
