from __future__ import annotations

import json
from pathlib import Path

from backend.benchmarks.publish_dashboard_benchmarks import build_publication_payload


def _artifact_path(name: str) -> Path:
    return Path("backend/benchmarks") / name


def test_build_publication_payload_emits_dashboard_shape() -> None:
    publication = build_publication_payload(
        [_artifact_path("example_benchmark_artifact.json")],
        note="test publication",
    )

    assert publication["note"] == "test publication"
    assert len(publication["runs"]) == 1
    run = publication["runs"][0]
    assert run["run_id"] == "dual_retrieval_benchmark-20260423T120000Z"
    assert run["mode"] == "semantic_only"
    assert "metrics" in run
    assert "freshness" in run
    assert "canonical_publication_source" in publication
    assert publication["canonical_publication_source"]["canonical_publication_owner"] == "ds_backend_local"
    assert "operator_publication" in publication
    assert publication["operator_publication"]["request_path_policy"] == "background_jobs_only"
    assert publication["operator_publication"]["trigger_label"] == "Update & publish"
    assert publication["operator_publication"]["trigger_modes"] == ["publish_existing", "execute_and_publish"]
    assert publication["operator_publication"]["required_job_states"] == [
        "queued",
        "running_benchmarks",
        "writing_artefacts",
        "publishing",
        "failed",
        "published",
    ]
    assert "phase_1_activation" in publication
    assert publication["phase_1_activation"]["required_modes"] == [
        "semantic_only",
        "coordinate_guided",
        "full_dss",
    ]
    assert publication["phase_1_activation"]["max_age_hours"] == 168


def test_build_publication_payload_merges_exemplars_by_run_id() -> None:
    publication = build_publication_payload(
        [_artifact_path("example_benchmark_artifact.json")],
        note="test publication",
        exemplars_by_run={
            "dual_retrieval_benchmark-20260423T120000Z": [
                {
                    "label": "Quantum networks retrieval proof",
                    "query": "Who is researching quantum networks?",
                    "coord": "37a8eec1:0673286f:WX-AURORA-01",
                }
            ]
        },
    )

    run = publication["runs"][0]
    assert len(run["exemplars"]) == 1
    assert run["exemplars"][0]["coord"] == "37a8eec1:0673286f:WX-AURORA-01"


def test_build_publication_payload_can_mark_reference_only_phase1_suites() -> None:
    publication = build_publication_payload(
        [_artifact_path("example_benchmark_artifact.json")],
        note="test publication",
        reference_only_suites=["HotpotQA", "LongMemEval"],
    )

    statuses = {item["suite_name"]: item["status"] for item in publication["phase_1_activation"]["suite_activation"]}
    assert statuses["HotpotQA"] == "reference_only"
    assert statuses["LongMemEval"] == "reference_only"
    assert statuses["MuSiQue"] == "planned"


def test_build_publication_payload_carries_phase1_freshness_settings() -> None:
    publication = build_publication_payload(
        [_artifact_path("example_benchmark_artifact.json")],
        note="test publication",
        max_age_hours=24,
    )

    assert publication["phase_1_activation"]["max_age_hours"] == 24


def test_build_publication_payload_sorts_newest_runs_first(tmp_path: Path) -> None:
    base = json.loads(_artifact_path("example_benchmark_artifact.json").read_text(encoding="utf-8"))
    older = dict(base)
    older["run_id"] = "dual_retrieval_benchmark-20260422T120000Z"
    older["executed_at"] = "2026-04-22T12:00:00Z"
    newer = dict(base)
    newer["run_id"] = "dual_retrieval_benchmark-20260424T120000Z"
    newer["executed_at"] = "2026-04-24T12:00:00Z"

    older_path = tmp_path / "older.json"
    newer_path = tmp_path / "newer.json"
    older_path.write_text(json.dumps(older), encoding="utf-8")
    newer_path.write_text(json.dumps(newer), encoding="utf-8")

    publication = build_publication_payload(
        [older_path, newer_path],
        note="test publication",
    )

    assert publication["runs"][0]["run_id"] == "dual_retrieval_benchmark-20260424T120000Z"
    assert publication["runs"][1]["run_id"] == "dual_retrieval_benchmark-20260422T120000Z"


def test_build_publication_payload_dedupes_by_suite_and_mode(tmp_path: Path) -> None:
    base = json.loads(_artifact_path("example_benchmark_artifact.json").read_text(encoding="utf-8"))
    older = dict(base)
    older["run_id"] = "dual_retrieval_benchmark-20260422T120000Z"
    older["executed_at"] = "2026-04-22T12:00:00Z"
    newer = dict(base)
    newer["run_id"] = "dual_retrieval_benchmark-20260424T120000Z"
    newer["executed_at"] = "2026-04-24T12:00:00Z"

    older_path = tmp_path / "older.json"
    newer_path = tmp_path / "newer.json"
    older_path.write_text(json.dumps(older), encoding="utf-8")
    newer_path.write_text(json.dumps(newer), encoding="utf-8")

    publication = build_publication_payload(
        [older_path, newer_path],
        note="test publication",
        dedupe_suite_mode=True,
    )

    assert len(publication["runs"]) == 1
    assert publication["runs"][0]["run_id"] == "dual_retrieval_benchmark-20260424T120000Z"
