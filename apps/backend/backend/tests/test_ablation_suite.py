"""Tests for the ablation runner and suite harness."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.benchmarks.ablation_runner import (
    ABLATION_CONDITIONS,
    AblationCondition,
    evaluate_ablation,
    run_ablation_condition,
)
from backend.benchmarks.ablation_suite import AblationSuiteReport, run_ablation_suite
from backend.benchmarks.run_dual_retrieval_benchmark import (
    BenchmarkMemoryService,
    load_dataset,
    seed_memories,
)


@pytest.fixture
def service_and_specs():
    dataset_path = Path("backend/benchmarks/benchmark_dataset.jsonl")
    service = BenchmarkMemoryService()
    seed_memories(service, dataset_path)
    _, specs = load_dataset(dataset_path)
    return service, specs


def test_semantic_only_baseline_has_perfect_recall(service_and_specs) -> None:
    service, specs = service_and_specs
    result = evaluate_ablation(
        service, specs, AblationCondition(name="semantic_only", mode="semantic_only")
    )
    assert result.summary.recall_at_10 == pytest.approx(1.0)
    assert result.summary.mrr == pytest.approx(1.0)
    assert result.breakdown.retrieval_ms
    assert result.breakdown.prompt_tokens


def test_coordinate_guided_produces_breakdown(service_and_specs) -> None:
    service, specs = service_and_specs
    result = evaluate_ablation(
        service, specs, AblationCondition(name="coordinate_guided", mode="coordinate_guided")
    )
    assert result.summary.queries == len(specs)
    assert result.breakdown.coordinate_resolution_ms
    assert result.breakdown.coordinate_lookup_tokens


def test_abstention_condition_runs_without_error(service_and_specs) -> None:
    service, specs = service_and_specs
    result = evaluate_ablation(
        service,
        specs,
        AblationCondition(
            name="abstention_on",
            mode="full_dss",
            use_abstention=True,
            abstention_threshold=0.35,
        ),
    )
    assert result.summary.queries == len(specs)


def test_run_ablation_condition_returns_valid_artifact() -> None:
    artifact = run_ablation_condition(
        AblationCondition(name="semantic_only", mode="semantic_only"), seed=7
    )
    assert artifact.status == "success"
    assert artifact.run_config["condition"] == "semantic_only"
    assert artifact.hardware is not None
    for group in ("retrieval", "latency", "cost", "traceability", "governance"):
        assert group in artifact.metrics
        assert artifact.metrics[group].status == "present"
    cost = artifact.metrics["cost"].metrics
    for metric in (
        "prompt_tokens",
        "completion_tokens",
        "coordinate_lookup_tokens",
        "retrieval_tokens",
        "coordinate_resolution_tokens",
        "llm_generation_tokens",
        "post_processing_tokens",
        "total_cost_usd",
    ):
        assert metric in cost
    assert cost["total_cost_usd"].unit == "usd"


def test_ablation_suite_runs_all_conditions(tmp_path: Path) -> None:
    report = run_ablation_suite(
        seeds=[193],
        output_root=tmp_path,
    )
    assert isinstance(report, AblationSuiteReport)
    assert len(report.conditions) == len(ABLATION_CONDITIONS)
    for cond in report.conditions:
        assert "retrieval" in cond
        assert "latency" in cond
        assert "cost" in cond

    report_file = list(tmp_path.glob("ablation_report_*.json"))
    assert report_file
    markdown_file = list(tmp_path.glob("ablation_report_*.md"))
    assert markdown_file
