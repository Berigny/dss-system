"""Tests for DSS-299 real-data track benchmark."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.benchmarks import dss299_real_data_track_benchmark as dss299
from backend.benchmarks.artifact_schema import validate_benchmark_artifact


def _config(tmp_path: Path, **overrides: object) -> dss299.BenchmarkConfig:
    defaults = {
        "output_root": tmp_path,
        "datasets": ("hotpotqa", "narrativeqa"),
        "samples_per_dataset": 3,
        "top_k": 3,
        "seeds": (193,),
        "coverage_gate": 0.8,
        "max_total_documents": 1000,
        "max_queries": 100,
        "max_embedding_calls": 2500,
        "budget_tokens": 500_000,
        "skip_real_embedding": True,
        "dry_run": True,
    }
    defaults.update(overrides)
    return dss299.BenchmarkConfig(**defaults)  # type: ignore[arg-type]


def test_synthetic_examples_are_deterministic() -> None:
    ex1 = dss299._load_synthetic_examples(3, seed=193)
    ex2 = dss299._load_synthetic_examples(3, seed=193)
    assert len(ex1) == 3
    assert [e.query_id for e in ex1] == [e.query_id for e in ex2]


def test_evaluate_produces_track_summary(tmp_path: Path) -> None:
    config = _config(tmp_path)
    examples = dss299._load_synthetic_examples(config.samples_per_dataset, seed=193)
    summary = dss299.evaluate(examples, config=config)
    assert summary.examples == 3
    assert summary.total_queries == 3
    assert "dss_qp_router" in summary.systems
    assert "bm25" in summary.systems
    assert summary.gate_pass or summary.coverage_score < config.coverage_gate


def test_budget_enforcement_trims_examples(tmp_path: Path) -> None:
    config = _config(tmp_path, budget_tokens=50)
    examples = dss299._load_synthetic_examples(10, seed=193)
    trimmed = dss299._enforce_budget(
        examples,
        max_total_documents=config.max_total_documents,
        max_queries=config.max_queries,
        budget_tokens=config.budget_tokens,
    )
    assert len(trimmed) < len(examples)


def test_build_artifact_validates_success_and_partial(tmp_path: Path) -> None:
    config = _config(tmp_path)
    examples = dss299._load_synthetic_examples(3, seed=193)
    summary = dss299.evaluate(examples, config=config)

    for gate_pass in (True, False):
        summary_forced = dss299.TrackSummary(
            examples=summary.examples,
            total_documents=summary.total_documents,
            total_queries=summary.total_queries,
            coverage_score=0.9 if gate_pass else 0.1,
            gate_pass=gate_pass,
            systems=summary.systems,
        )
        artifact = dss299._build_artifact(
            summary_forced,
            config=config,
            executed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            runtime_ms=1.0,
            seed=193,
            status="success" if gate_pass else "partial",
            gate_failure_reason=None if gate_pass else "gate not passed",
        )
        validated = validate_benchmark_artifact(artifact.model_dump(mode="json"))
        assert validated.status == ("success" if gate_pass else "partial")
        if not gate_pass:
            assert validated.metrics["cost"].status == "absent"


def test_run_benchmark_emits_aggregate_artifact(tmp_path: Path) -> None:
    config = _config(tmp_path, seeds=(193, 42))
    aggregate = dss299.run_benchmark(config)
    assert aggregate.status in {"success", "partial"}
    assert aggregate.suite_id == "dss299-real-data-track"
    assert "dss_qp_router_recall_at_1" in aggregate.metrics["retrieval"].metrics
    stats = aggregate.metrics["retrieval"].metrics["dss_qp_router_recall_at_1"].statistics
    assert stats is not None
    assert stats.sample_count == 2


def test_cli_smoke(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    script = "apps/backend/backend/benchmarks/dss299_real_data_track_benchmark.py"
    repo_root = Path(__file__).parent.parent.parent.parent.parent
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "apps" / "backend")
    result = subprocess.run(
        [
            sys.executable,
            script,
            "--dry-run",
            "--skip-real-embedding",
            "--samples-per-dataset", "2",
            "--seeds", "193",
            "--output-root", str(tmp_path),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "aggregate").exists()
