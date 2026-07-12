from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.benchmarks.run_shadow_replay_benchmark import (
    load_shadow_samples,
    run_shadow_replay,
    write_artifacts,
)


def _sample_path(name: str) -> Path:
    return Path("backend/benchmarks") / name


def test_load_shadow_samples_reads_example_fixture() -> None:
    samples = load_shadow_samples(_sample_path("example_shadow_replay_samples.json"))

    assert len(samples) == 2
    assert samples[0].sample_id == "shadow-001"
    assert samples[0].query == "Who is researching quantum networks?"


def test_run_shadow_replay_emits_artifact_per_mode() -> None:
    samples = load_shadow_samples(_sample_path("example_shadow_replay_samples.json"))
    executed_at = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)

    artifacts = run_shadow_replay(
        samples,
        repo_sha="484defc",
        executed_at=executed_at,
    )

    assert [artifact.mode for artifact in artifacts] == [
        "semantic_only",
        "coordinate_guided",
        "full_dss",
    ]
    for artifact in artifacts:
        assert artifact.run_config["evidence_source"] == "shadow_replay"
        assert artifact.run_config["sample_count"] == 2
        assert artifact.status in {"partial", "success"}


def test_run_shadow_replay_marks_failures_explicitly() -> None:
    samples = load_shadow_samples(_sample_path("example_shadow_replay_samples.json"))
    broken = [sample.model_copy(update={"memories": []}) for sample in samples]

    artifacts = run_shadow_replay(
        broken,
        repo_sha="484defc",
        executed_at=datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc),
    )

    assert len(artifacts) == 3
    assert all(artifact.status == "failed" for artifact in artifacts)
    assert all(artifact.failure_reason for artifact in artifacts)


def test_write_artifacts_serializes_shadow_replay_outputs(tmp_path: Path) -> None:
    samples = load_shadow_samples(_sample_path("example_shadow_replay_samples.json"))
    artifacts = run_shadow_replay(
        samples,
        repo_sha="484defc",
        executed_at=datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc),
    )

    paths = write_artifacts(artifacts, tmp_path)

    assert len(paths) == 3
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["suite_id"] == "shadow_replay_benchmark"


def test_load_shadow_samples_rejects_invalid_shape(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps({"samples": [{"sample_id": "x"}]}), encoding="utf-8")

    with pytest.raises(Exception):
        load_shadow_samples(bad_path)
