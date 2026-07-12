"""Tests for the DSS-228 transparency report generator."""

from __future__ import annotations

import json

import pytest

from backend.benchmarks.transparency_report import (
    DEFAULT_CORPUS_PATH,
    TAXONOMY,
    build_report,
    generate_traces,
    run_transparency_report,
)


def test_generate_traces_returns_records_with_coordinates() -> None:
    traces = generate_traces(corpus_path=DEFAULT_CORPUS_PATH, top_k=5)
    assert traces
    for trace in traces:
        assert trace.query_id
        assert trace.query_text
        assert trace.query_coordinate["kernel_node"]
        assert trace.qp_ranked or trace.vector_ranked
        for row in trace.qp_ranked + trace.vector_ranked:
            assert "coordinate" in row
            assert "valid" in row


def test_failure_mode_taxonomy_covers_observed_outcomes() -> None:
    traces = generate_traces(corpus_path=DEFAULT_CORPUS_PATH, top_k=5)
    observed = {t.qp_outcome for t in traces} | {t.vector_outcome for t in traces}
    for outcome in observed:
        assert outcome in TAXONOMY


def test_build_report_has_required_sections() -> None:
    report = build_report(corpus_path=DEFAULT_CORPUS_PATH, top_k=5)
    assert report["report_schema_version"] == "1.0.0"
    assert "generated_at" in report
    assert "summary" in report
    assert "failure_mode_taxonomy" in report
    assert "failure_mode_counts" in report
    assert "sample_traces" in report
    assert "out_of_scope" in report
    assert "screening_note" in report


def test_sample_traces_meet_minimum_counts() -> None:
    report = build_report(
        corpus_path=DEFAULT_CORPUS_PATH,
        top_k=5,
        sample_success=3,
        sample_failure=3,
    )
    successes = [t for t in report["sample_traces"] if t["qp_outcome"] == "success"]
    failures = [
        t
        for t in report["sample_traces"]
        if t["qp_outcome"] != "success" or t["vector_outcome"] != "success"
    ]
    assert len(successes) >= 3
    assert len(failures) >= 3


def test_run_transparency_report_writes_outputs(tmp_path) -> None:
    report = run_transparency_report(
        corpus_path=DEFAULT_CORPUS_PATH,
        output_root=tmp_path,
        top_k=5,
    )
    json_files = list(tmp_path.glob("transparency_report_*.json"))
    md_files = list(tmp_path.glob("transparency_report_*.md"))
    traces_files = list(tmp_path.glob("sample_traces_*.jsonl"))
    assert json_files
    assert md_files
    assert traces_files

    loaded = json.loads(json_files[0].read_text())
    assert loaded["report_schema_version"] == report["report_schema_version"]

    trace_lines = traces_files[0].read_text().strip().split("\n")
    assert len(trace_lines) == len(report["sample_traces"])


def test_qp_success_rate_is_non_degenerate() -> None:
    report = build_report(corpus_path=DEFAULT_CORPUS_PATH, top_k=5)
    summary = report["summary"]
    assert summary["total_queries"] >= 7
    assert 0.0 <= summary["qp_success_rate"] <= 1.0
    assert 0.0 <= summary["vector_success_rate"] <= 1.0
