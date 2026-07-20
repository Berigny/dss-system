"""Tests for DSS-297 citation-faithfulness benchmark."""

from __future__ import annotations

import pytest

from backend.benchmarks.artifact_schema import validate_benchmark_artifact
from backend.benchmarks.dss297_citation_faithfulness_benchmark import (
    BenchmarkConfig,
    _build_ledger_store,
    _build_source_entries,
    _generate_cases,
    _verify_case,
    evaluate,
    run_benchmark,
    run_single_seed,
)


def test_build_source_entries_populated() -> None:
    entries = _build_source_entries()
    assert len(entries) >= 3
    assert all(e.entry_id and e.text and e.claims for e in entries)


def test_ledger_store_commits_with_chain() -> None:
    entries = _build_source_entries()
    store = _build_ledger_store(entries)
    status = store.verify_namespace_chain("dss297")
    assert status["valid"] is True
    assert status["entries_checked"] == len(entries)


def test_faithful_case_passes() -> None:
    entries = _build_source_entries()
    store = _build_ledger_store(entries)
    cases = _generate_cases(__import__("random").Random(193))
    faithful = next(c for c in cases if c.case_id == "faithful_single")
    result = _verify_case(faithful, store, entries)
    assert result.citation_integrity == 1.0
    assert result.chain_valid is True
    assert not result.missing
    assert not result.unexpected


def test_missing_ref_case_fails() -> None:
    entries = _build_source_entries()
    store = _build_ledger_store(entries)
    cases = _generate_cases(__import__("random").Random(193))
    case = next(c for c in cases if c.case_id == "missing_ref")
    result = _verify_case(case, store, entries)
    assert result.citation_integrity == 0.0
    assert result.missing


def test_unexpected_ref_case_fails() -> None:
    entries = _build_source_entries()
    store = _build_ledger_store(entries)
    cases = _generate_cases(__import__("random").Random(193))
    case = next(c for c in cases if c.case_id == "unexpected_ref")
    result = _verify_case(case, store, entries)
    assert result.citation_integrity == 0.0
    assert result.unexpected


def test_second_source_case_passes() -> None:
    entries = _build_source_entries()
    store = _build_ledger_store(entries)
    cases = _generate_cases(__import__("random").Random(193))
    case = next(c for c in cases if c.case_id == "second_source")
    result = _verify_case(case, store, entries)
    assert result.citation_integrity == 1.0
    assert "dss297:gaap-606" in result.matched


def test_evaluate_returns_summary() -> None:
    summary = evaluate(seed=193)
    assert summary.cases > 0
    assert 0.0 <= summary.citation_integrity <= 1.0
    assert 0.0 <= summary.chain_valid_rate <= 1.0
    assert 0.0 <= summary.judge_score_mean <= 1.0


def test_single_seed_artifact_validates(tmp_path) -> None:
    config = BenchmarkConfig(
        output_root=tmp_path,
        seeds=(193,),
    )
    artifact = run_single_seed(193, config)
    assert artifact.status == "success"
    payload = artifact.model_dump(mode="json")
    validated = validate_benchmark_artifact(payload)
    assert validated.status == "success"
    assert "citation_integrity" in validated.metrics["retrieval"].metrics
    assert validated.metrics["cost"].metrics["llm_calls"].value == 0


def test_multi_seed_benchmark_produces_aggregate(tmp_path) -> None:
    config = BenchmarkConfig(
        output_root=tmp_path,
        seeds=(193, 42),
    )
    aggregate = run_benchmark(config)
    assert aggregate.status == "success"
    assert aggregate.run_config.get("aggregate") is True
    assert aggregate.run_config.get("seed_count") == 2
    assert "citation_integrity" in aggregate.metrics["retrieval"].metrics
