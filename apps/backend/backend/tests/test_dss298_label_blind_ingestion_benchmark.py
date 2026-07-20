"""Tests for DSS-298 label-blind ingestion benchmark."""

from __future__ import annotations

import pytest

from backend.benchmarks.artifact_schema import validate_benchmark_artifact
from backend.benchmarks.dss298_label_blind_ingestion_benchmark import (
    BenchmarkConfig,
    DOCUMENTS,
    QUERIES,
    _compute_coverage,
    _derive_coordinate,
    evaluate,
    run_benchmark,
    run_single_seed,
)


def test_derive_coordinate_returns_qpcoordinate_for_known_concepts() -> None:
    coord = _derive_coordinate(DOCUMENTS[0])
    assert coord is not None
    assert coord.kernel_node is not None
    assert coord.metric_prime is not None


def test_derive_coordinate_returns_none_for_empty_text() -> None:
    assert _derive_coordinate("") is None


def test_compute_coverage_counts_compatible_queries() -> None:
    doc_coords = [_derive_coordinate(text) for text in DOCUMENTS]
    query_coords = [_derive_coordinate(text) for text in QUERIES]
    compatible, total = _compute_coverage(doc_coords, query_coords)
    valid_queries = sum(1 for c in query_coords if c is not None)
    assert total == valid_queries
    assert 0 <= compatible <= total


def test_evaluate_reports_coverage_and_gate() -> None:
    result = evaluate(coverage_gate=0.8, transport="R1")
    assert 0.0 <= result.coverage_score <= 1.0
    assert 0 < result.total_queries <= len(QUERIES)
    assert result.status in ("supported", "exploratory")
    assert result.gate_pass == (result.coverage_score >= 0.8)


def test_evaluate_gate_passes_at_zero() -> None:
    result = evaluate(coverage_gate=0.0, transport="R1")
    assert result.gate_pass is True
    assert result.status == "supported"


def test_single_seed_artifact_validates(tmp_path) -> None:
    config = BenchmarkConfig(
        output_root=tmp_path,
        coverage_gate=0.8,
        seeds=(193,),
        transport="R1",
    )
    artifact = run_single_seed(193, config)
    assert artifact.status == "success"
    payload = artifact.model_dump(mode="json")
    validated = validate_benchmark_artifact(payload)
    assert validated.status == "success"
    assert "coverage_score" in validated.metrics["retrieval"].metrics
    assert validated.metrics["cost"].metrics["llm_calls"].value == 0
    assert "hugooconnor" in (artifact.run_config.get("credit") or "")


def test_multi_seed_benchmark_produces_aggregate(tmp_path) -> None:
    config = BenchmarkConfig(
        output_root=tmp_path,
        coverage_gate=0.8,
        seeds=(193, 42),
        transport="R1",
    )
    aggregate = run_benchmark(config)
    assert aggregate.status == "success"
    assert aggregate.run_config.get("aggregate") is True
    assert aggregate.run_config.get("seed_count") == 2
    assert "coverage_score" in aggregate.metrics["retrieval"].metrics
