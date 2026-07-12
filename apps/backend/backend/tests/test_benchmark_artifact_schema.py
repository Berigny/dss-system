from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.benchmarks.artifact_schema import validate_benchmark_artifact
from backend.benchmarks.run_dual_retrieval_benchmark import (
    BenchmarkMemoryService,
    PHASE1_ALL_SUITES,
    PHASE1_MEMORY_SUITES,
    PHASE1_RETRIEVAL_SUITES,
    BenchmarkResult,
    RunnerConfig,
    build_artifact,
    build_failed_artifact,
    load_dataset,
    output_path_for_run,
    ranked_results_for_mode,
    seed_memories,
)


def test_example_benchmark_artifact_validates() -> None:
    path = Path("backend/benchmarks/example_benchmark_artifact.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    artifact = validate_benchmark_artifact(payload)

    assert artifact.status == "partial"
    assert artifact.mode == "semantic_only"
    assert artifact.metrics["retrieval"].status == "present"
    assert artifact.metrics["traceability"].absence_reason is not None
    assert artifact.repos[0].name == "ds-backend-local"


def test_build_artifact_emits_required_metric_groups() -> None:
    artifact = build_artifact(
        BenchmarkResult(recall_at_1=0.4, recall_at_5=0.7, recall_at_10=0.75, mrr=0.6, avg_latency_ms=22.5, token_cost=144.0, queries=8),
        config=RunnerConfig(
            mode="full_dss",
            suite_id="dual_retrieval_benchmark",
            suite_version="v1",
            dataset_version="local-v1",
            top_k=10,
        ),
        dataset_path=Path("backend/benchmarks/benchmark_dataset.jsonl"),
        executed_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
        repo_sha="abc1234",
        artefact_schema_version="1.0.0",
    )

    payload = artifact.model_dump(mode="json")

    assert set(payload["metrics"].keys()) == {
        "retrieval",
        "traceability",
        "governance",
        "latency",
        "cost",
    }
    assert payload["status"] == "partial"
    assert payload["mode"] == "full_dss"
    assert payload["metrics"]["retrieval"]["metrics"]["recall_at_1"]["value"] == 0.4
    assert payload["metrics"]["retrieval"]["metrics"]["recall_at_5"]["value"] == 0.7
    assert payload["metrics"]["retrieval"]["metrics"]["recall_at_10"]["value"] == 0.75
    assert payload["metrics"]["latency"]["metrics"]["avg_latency_ms"]["value"] == 22.5
    assert payload["metrics"]["cost"]["metrics"]["token_cost"]["value"] == 144.0
    assert payload["metrics"]["traceability"]["absence_reason"] == "dual_retrieval_runner_does_not_measure_traceability_yet"


def test_validation_rejects_silent_missing_metric_group() -> None:
    payload = json.loads(Path("backend/benchmarks/example_benchmark_artifact.json").read_text(encoding="utf-8"))
    payload["metrics"].pop("cost")

    with pytest.raises(ValidationError):
        validate_benchmark_artifact(payload)


def test_failed_artifact_requires_failure_reason_and_keeps_mode_context() -> None:
    artifact = build_failed_artifact(
        config=RunnerConfig(
            mode="coordinate_guided",
            suite_id="dual_retrieval_benchmark",
            suite_version="v1",
            dataset_version="local-v1",
            top_k=5,
        ),
        dataset_path=Path("backend/benchmarks/benchmark_dataset.jsonl"),
        executed_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
        repo_sha="abc1234",
        artefact_schema_version="1.0.0",
        failure_reason="dataset_parse_error",
    )

    assert artifact.status == "failed"
    assert artifact.failure_reason == "dataset_parse_error"
    assert artifact.mode == "coordinate_guided"


def test_output_path_for_run_is_deterministic_by_suite_version_mode_and_time() -> None:
    path = output_path_for_run(
        Path("/tmp/benchmarks"),
        config=RunnerConfig(
            mode="semantic_only",
            suite_id="dual_retrieval_benchmark",
            suite_version="v1",
            dataset_version="local-v1",
            top_k=10,
        ),
        executed_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
    )

    assert str(path).endswith(
        "/dual_retrieval_benchmark/v1/semantic_only/20260423T120000Z.json"
    )


def test_runner_modes_execute_same_suite_with_explicit_mode_variants() -> None:
    service = BenchmarkMemoryService()
    dataset_path = Path("backend/benchmarks/benchmark_dataset.jsonl")
    seed_memories(service, dataset_path)
    _, specs = load_dataset(dataset_path)
    first_query = specs[0]

    semantic_results = ranked_results_for_mode(service, first_query, mode="semantic_only", top_k=3)
    coordinate_results = ranked_results_for_mode(service, first_query, mode="coordinate_guided", top_k=3)
    full_results = ranked_results_for_mode(service, first_query, mode="full_dss", top_k=3)

    assert len(semantic_results) == 3
    assert len(coordinate_results) == 3
    assert len(full_results) == 3
    assert semantic_results[0]["text"]
    assert coordinate_results[0]["text"]
    assert full_results[0]["text"]


def test_phase1_retrieval_suite_presets_are_defined() -> None:
    assert set(PHASE1_RETRIEVAL_SUITES.keys()) == {"MuSiQue", "HotpotQA", "2WikiMultiHopQA"}
    assert PHASE1_RETRIEVAL_SUITES["MuSiQue"].dataset_filename == "musique_phase1_dataset.jsonl"


def test_phase1_memory_suite_presets_are_defined() -> None:
    assert set(PHASE1_MEMORY_SUITES.keys()) == {"LongMemEval", "LoCoMo"}
    assert PHASE1_MEMORY_SUITES["LongMemEval"].dataset_filename == "longmemeval_phase1_dataset.jsonl"
    assert set(PHASE1_ALL_SUITES.keys()) == {
        "MuSiQue",
        "HotpotQA",
        "2WikiMultiHopQA",
        "LongMemEval",
        "LoCoMo",
    }
