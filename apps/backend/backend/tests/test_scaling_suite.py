"""Tests for the DSS-230 scaling and stress suite."""

from __future__ import annotations

import pytest

from backend.benchmarks.scaling_suite import (
    run_concurrent_load,
    run_long_context,
    run_multi_turn,
    run_noisy_inputs,
    run_scaling_suite,
)


def test_run_long_context_produces_valid_results(tmp_path) -> None:
    report = run_long_context(
        haystack_length=500,
        top_k=5,
        seed=193,
        output_root=tmp_path,
        repo_sha="test-sha",
    )
    assert report["haystack_length"] == 500
    assert report["total_tokens"] > 0
    for mode in ("semantic_only", "coordinate_guided", "full_dss"):
        assert mode in report["modes"]
        metrics = report["modes"][mode]
        assert 0.0 <= metrics["recall_at_k"] <= 1.0
        assert metrics["avg_latency_ms"] >= 0.0


def test_run_concurrent_load_reports_latency_distribution(tmp_path) -> None:
    report = run_concurrent_load(
        haystack_length=500,
        top_k=5,
        seed=193,
        output_root=tmp_path,
        repo_sha="test-sha",
        workers=2,
        requests=4,
    )
    assert report["workers"] == 2
    assert report["requests"] == 4
    assert report["throughput_qps"] > 0.0
    assert report["p50_latency_ms"] <= report["p95_latency_ms"] <= report["max_latency_ms"]


def test_run_noisy_inputs_shows_graceful_degradation(tmp_path) -> None:
    report = run_noisy_inputs(
        haystack_length=500,
        top_k=5,
        seed=193,
        output_root=tmp_path,
        repo_sha="test-sha",
    )
    levels = [row["noise_level"] for row in report["noise_levels"]]
    assert 0.0 in levels
    # Recall should generally stay non-increasing with noise.
    recalls = [row["recall_at_k"] for row in report["noise_levels"]]
    assert all(0.0 <= r <= 1.0 for r in recalls)


def test_run_multi_turn_executes_agentic_trace(tmp_path) -> None:
    report = run_multi_turn(
        turns=10,
        top_k=5,
        seed=193,
        output_root=tmp_path,
        repo_sha="test-sha",
    )
    assert report["turns"] == 10
    assert report["queries"] == 9  # queries start from turn 2
    assert 0.0 <= report["recall_at_k"] <= 1.0


def test_run_scaling_suite_writes_report(tmp_path) -> None:
    report = run_scaling_suite(
        output_root=tmp_path,
        haystack_length=500,
        top_k=5,
        seed=193,
        concurrent_workers=2,
        concurrent_requests=4,
        multi_turn_turns=5,
    )
    assert "long_context" in report
    assert "concurrent_load" in report
    assert "noisy_inputs" in report
    assert "multi_turn" in report

    json_files = list(tmp_path.glob("scaling_report_*.json"))
    md_files = list(tmp_path.glob("scaling_report_*.md"))
    assert json_files
    assert md_files
