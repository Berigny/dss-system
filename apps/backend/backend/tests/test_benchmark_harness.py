"""Tests for the multi-seed benchmark harness and hardware profiler."""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.benchmarks.artifact_schema import (
    BenchmarkArtifact,
    BenchmarkMode,
    MetricGroup,
)
from backend.benchmarks.hardware import HardwareProfile, detect_hardware_profile
from backend.benchmarks.harness import BenchmarkHarness, run_benchmark_suite


def _make_artifact(
    *,
    seed: int,
    recall: float,
    latency: float,
    cost: float,
    status: str = "partial",
    failure_reason: str | None = None,
) -> BenchmarkArtifact:
    if status == "failed" and not failure_reason:
        failure_reason = "simulated failure"
    return BenchmarkArtifact(
        artefact_schema_version="1.0.0",
        run_id=f"test-run-seed-{seed}",
        suite_id="test_suite",
        suite_version="v1",
        executed_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        mode="coordinate_guided",
        status=status,  # type: ignore[arg-type]
        failure_reason=failure_reason,
        repos=[
            {
                "name": "ds-backend-local",
                "commit_sha": "abc1234",
                "role": "canonical_benchmark_engine",
                "required_for_run": True,
            }
        ],
        datasets=[
            {"name": "test_dataset", "version": "v1", "split": "benchmark"}
        ],
        metrics={
            "retrieval": {
                "status": "present",
                "metrics": {
                    "recall_at_1": {
                        "value": recall,
                        "unit": "ratio",
                        "description": "Recall at rank 1.",
                    }
                },
            },
            "latency": {
                "status": "present",
                "metrics": {
                    "avg_latency_ms": {
                        "value": latency,
                        "unit": "ms",
                        "description": "Average latency.",
                    }
                },
            },
            "cost": {
                "status": "present",
                "metrics": {
                    "token_cost": {
                        "value": cost,
                        "unit": "tokens",
                        "description": "Token cost.",
                    }
                },
            },
            "traceability": {
                "status": "absent",
                "absence_reason": "not measured",
            },
            "governance": {
                "status": "absent",
                "absence_reason": "not measured",
            },
        },
        freshness={
            "status": "fresh",
            "checked_at": datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            "max_age_hours": 24,
            "age_hours": 0.0,
        },
        run_config={"seed": seed},
    )


def test_harness_runs_all_seeds_and_creates_artifacts(tmp_path: Path) -> None:
    seeds = [1, 2, 3]

    def runner(seed: int) -> BenchmarkArtifact:
        return _make_artifact(seed=seed, recall=0.5 + seed * 0.05, latency=10.0, cost=100)

    harness = BenchmarkHarness(
        suite_id="test_suite",
        suite_version="v1",
        mode="coordinate_guided",
        seeds=seeds,
        output_root=tmp_path,
    )
    aggregate = harness.run(runner)

    assert aggregate.status == "partial"
    assert aggregate.run_config.get("aggregate") is True
    assert aggregate.run_config.get("seed_count") == 3
    assert aggregate.hardware is not None

    seed_dir = tmp_path / "seeds"
    assert seed_dir.exists()
    for seed in seeds:
        assert (seed_dir / str(seed)).exists()
        files = list((seed_dir / str(seed)).glob("*.json"))
        assert len(files) == 1

    aggregate_files = list((tmp_path / "aggregate").glob("*.json"))
    assert len(aggregate_files) == 1


def test_aggregate_statistics_are_accurate(tmp_path: Path) -> None:
    recalls = [0.5, 0.6, 0.7]

    def runner(seed: int) -> BenchmarkArtifact:
        return _make_artifact(
            seed=seed,
            recall=recalls[seed - 1],
            latency=10.0,
            cost=100,
        )

    aggregate = run_benchmark_suite(
        runner,
        suite_id="test_suite",
        suite_version="v1",
        mode="coordinate_guided",
        seeds=[1, 2, 3],
        output_root=tmp_path,
    )

    retrieval = aggregate.metrics["retrieval"]
    assert retrieval.status == "present"
    stats = retrieval.metrics["recall_at_1"].statistics
    assert stats is not None
    assert stats.sample_count == 3
    assert stats.mean == pytest.approx(statistics.mean(recalls))
    assert stats.standard_deviation == pytest.approx(statistics.stdev(recalls))
    assert stats.min == pytest.approx(min(recalls))
    assert stats.max == pytest.approx(max(recalls))
    assert stats.ci_95_low < stats.mean < stats.ci_95_high


def test_aggregate_status_rolls_up_failed_seeds(tmp_path: Path) -> None:
    calls: list[int] = []

    def runner(seed: int) -> BenchmarkArtifact:
        calls.append(seed)
        status = "failed" if seed == 2 else "partial"
        return _make_artifact(seed=seed, recall=0.5, latency=10.0, cost=100, status=status)

    harness = BenchmarkHarness(
        suite_id="test_suite",
        suite_version="v1",
        mode="coordinate_guided",
        seeds=[1, 2, 3],
        output_root=tmp_path,
    )
    aggregate = harness.run(runner)
    assert aggregate.status == "failed"
    assert calls == [1, 2, 3]


def test_harness_requires_at_least_one_seed() -> None:
    with pytest.raises(ValueError, match="at least one seed"):
        BenchmarkHarness(
            suite_id="x",
            suite_version="v1",
            mode="coordinate_guided",
            seeds=[],
            output_root=Path("/tmp"),
        )


def test_hardware_profile_is_detected_and_serializable() -> None:
    profile = detect_hardware_profile()
    assert profile.platform
    assert profile.backend_accelerator in {"cuda", "mps", "cpu"}
    payload = profile.to_dict()
    assert "platform" in payload
    # Round-trip through the schema model used by artefacts.
    from backend.benchmarks.artifact_schema import HardwareProfile as SchemaHardwareProfile

    schema_profile = SchemaHardwareProfile(**payload)
    assert schema_profile.backend_accelerator == profile.backend_accelerator


def test_seed_artifacts_include_hardware_and_seed(tmp_path: Path) -> None:
    def runner(seed: int) -> BenchmarkArtifact:
        return _make_artifact(seed=seed, recall=0.5, latency=10.0, cost=100)

    harness = BenchmarkHarness(
        suite_id="test_suite",
        suite_version="v1",
        mode="coordinate_guided",
        seeds=[7],
        output_root=tmp_path,
    )
    harness.run(runner)

    seed_file = next((tmp_path / "seeds" / "7").glob("*.json"))
    payload = json.loads(seed_file.read_text(encoding="utf-8"))
    assert payload["run_config"]["seed"] == 7
    assert payload["hardware"]["backend_accelerator"] in {"cuda", "mps", "cpu"}
