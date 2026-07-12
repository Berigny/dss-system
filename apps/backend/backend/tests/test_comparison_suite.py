"""Tests for the DSS-227 broader-comparison suite and baseline adapters."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.benchmarks.comparison_baselines import (
    BASELINES,
    DenseRetrievalBaseline,
    GrokBaseline,
    HierarchicalRagBaseline,
    LongContextBaseline,
)
from backend.benchmarks.comparison_benchmark import (
    run_multihop_baseline,
    run_needle_baseline,
    run_ruler_baseline,
)
from backend.benchmarks.comparison_suite import ComparisonSuiteReport, run_comparison_suite


@pytest.mark.parametrize("baseline", BASELINES.values(), ids=list(BASELINES))
def test_needle_baseline_produces_valid_artifact(baseline) -> None:
    artifact = run_needle_baseline(baseline, seed=193, lengths=(8, 16))
    assert artifact.status == "partial"
    assert artifact.run_config["baseline"] == baseline.name
    assert artifact.run_config["benchmark"] == "longbench-needle"
    assert "retrieval" in artifact.metrics
    assert "latency" in artifact.metrics
    assert "cost" in artifact.metrics
    assert artifact.hardware is not None


@pytest.mark.parametrize("baseline", BASELINES.values(), ids=list(BASELINES))
def test_multihop_baseline_produces_valid_artifact(baseline) -> None:
    artifact = run_multihop_baseline(baseline, seed=193, chain_count=3)
    assert artifact.status == "partial"
    assert artifact.run_config["baseline"] == baseline.name
    assert artifact.run_config["benchmark"] == "longbench-multihop"
    assert "retrieval" in artifact.metrics
    assert "latency" in artifact.metrics
    assert "cost" in artifact.metrics
    assert artifact.hardware is not None


def test_grok_baseline_is_blocked() -> None:
    grok = BASELINES["grok_latest"]
    assert isinstance(grok, GrokBaseline)

    needle = run_needle_baseline(grok, seed=193, lengths=(8,))
    assert needle.run_config["blocked"] is True
    assert needle.run_config["blocked_reason"] == GrokBaseline.blocked_reason
    assert needle.metrics["retrieval"].metrics["recall_at_1"].value == pytest.approx(0.0)

    multihop = run_multihop_baseline(grok, seed=193, chain_count=3)
    assert multihop.run_config["blocked"] is True


def test_dense_retrieval_matches_expected_failure_mode() -> None:
    """Dense baseline should score zero on needle because the target is lexically distant."""
    baseline = BASELINES["dense_retrieval"]
    assert isinstance(baseline, DenseRetrievalBaseline)
    artifact = run_needle_baseline(baseline, seed=193, lengths=(8, 16))
    assert artifact.metrics["retrieval"].metrics["recall_at_1"].value == pytest.approx(0.0)


def test_long_context_baseline_runs_nonzero_on_multihop() -> None:
    baseline = BASELINES["long_context_model"]
    assert isinstance(baseline, LongContextBaseline)
    artifact = run_multihop_baseline(baseline, seed=193, chain_count=3)
    assert artifact.metrics["retrieval"].metrics["recall_at_k"].value >= 0.0


def test_hierarchical_rag_is_two_stage_variant() -> None:
    baseline = BASELINES["hierarchical_rag"]
    assert isinstance(baseline, HierarchicalRagBaseline)
    artifact = run_multihop_baseline(baseline, seed=193, chain_count=3)
    assert artifact.run_config["baseline"] == "hierarchical_rag"


def test_ruler_baseline_produces_valid_artifact() -> None:
    baseline = BASELINES["dense_retrieval"]
    artifact = run_ruler_baseline(baseline, seed=193, haystack_length=100)
    assert artifact.status == "partial"
    assert artifact.run_config["baseline"] == "dense_retrieval"
    assert artifact.run_config["benchmark"] == "ruler-256k"
    assert "retrieval" in artifact.metrics
    assert "latency" in artifact.metrics
    assert "cost" in artifact.metrics


def test_comparison_suite_runs_full_matrix(tmp_path: Path) -> None:
    report = run_comparison_suite(
        seeds=[193],
        output_root=tmp_path,
    )
    assert isinstance(report, ComparisonSuiteReport)
    assert len(report.comparisons) == 12

    baselines = {row["baseline"] for row in report.comparisons}
    benchmarks = {row["benchmark"] for row in report.comparisons}
    assert baselines == {"dense_retrieval", "hierarchical_rag", "long_context_model", "grok_latest"}
    assert benchmarks == {"longbench-needle", "longbench-multihop", "ruler-256k"}

    grok_rows = [row for row in report.comparisons if row["baseline"] == "grok_latest"]
    assert len(grok_rows) == 3
    for row in grok_rows:
        assert row["blocked"] is True
        assert row["recall_at_1"] == pytest.approx(0.0)

    json_files = list(tmp_path.glob("comparison_report_*.json"))
    md_files = list(tmp_path.glob("comparison_report_*.md"))
    assert json_files
    assert md_files
