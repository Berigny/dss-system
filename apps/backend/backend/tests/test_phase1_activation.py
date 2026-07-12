from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from backend.benchmarks.phase1_activation import (
    PHASE1_REQUIRED_METRICS,
    PHASE1_REQUIRED_MODES,
    build_phase1_activation_contract,
)


def _base_artifact() -> dict:
    path = Path("backend/benchmarks/example_benchmark_artifact.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _phase1_artifact(*, suite_id: str, mode: str) -> dict:
    payload = deepcopy(_base_artifact())
    payload["run_id"] = f"{suite_id}-{mode}-20260426T120000Z"
    payload["suite_id"] = suite_id
    payload["mode"] = mode
    payload["status"] = "partial"
    payload["datasets"][0]["name"] = suite_id.lower()
    payload["metrics"]["retrieval"]["metrics"]["recall_at_1"] = {
        "value": 0.42,
        "unit": "ratio",
        "description": "Fraction of benchmark queries with a relevant item in the top result.",
    }
    payload["metrics"]["retrieval"]["metrics"]["recall_at_5"] = {
        "value": 0.73,
        "unit": "ratio",
        "description": "Fraction of benchmark queries with a relevant item in the top five results.",
    }
    payload["metrics"]["cost"] = {
        "status": "present",
        "metrics": {
            "token_cost": {
                "value": 142.0,
                "unit": "tokens",
                "description": "Token cost for the benchmark run.",
            }
        },
    }
    return payload


def test_phase1_activation_contract_freezes_named_suite_set_and_requirements() -> None:
    checked_at = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
    contract = build_phase1_activation_contract([], checked_at=checked_at)

    assert contract["phase"] == "phase_1"
    assert contract["publication_checked_at"] == checked_at.isoformat()
    assert contract["suite_families"]["retrieval_and_multihop"] == ["MuSiQue", "HotpotQA", "2WikiMultiHopQA"]
    assert contract["suite_families"]["long_memory"] == ["LongMemEval", "LoCoMo", "RULER256K"]
    assert contract["required_modes"] == list(PHASE1_REQUIRED_MODES)
    assert contract["required_metrics"] == list(PHASE1_REQUIRED_METRICS)
    assert all(item["status"] == "planned" for item in contract["suite_activation"])
    assert all(item["freshness_status"] == "unpublished" for item in contract["suite_activation"])


def test_phase1_activation_marks_suite_active_only_when_all_modes_and_metrics_are_present() -> None:
    artifacts = [
        _phase1_artifact(suite_id="HotpotQA", mode="semantic_only"),
        _phase1_artifact(suite_id="HotpotQA", mode="coordinate_guided"),
        _phase1_artifact(suite_id="HotpotQA", mode="full_dss"),
    ]

    checked_at = datetime(2026, 4, 26, 13, 0, tzinfo=timezone.utc)
    contract = build_phase1_activation_contract(artifacts, checked_at=checked_at)
    hotpot = next(item for item in contract["suite_activation"] if item["suite_name"] == "HotpotQA")

    assert hotpot["status"] == "active"
    assert hotpot["freshness_status"] == "fresh"
    assert hotpot["published_modes"] == ["coordinate_guided", "full_dss", "semantic_only"]
    assert hotpot["required_metrics"] == list(PHASE1_REQUIRED_METRICS)


def test_phase1_activation_uses_pending_and_reference_only_states_explicitly() -> None:
    artifacts = [_phase1_artifact(suite_id="MuSiQue", mode="semantic_only")]

    checked_at = datetime(2026, 4, 26, 13, 0, tzinfo=timezone.utc)
    contract = build_phase1_activation_contract(
        artifacts,
        reference_only_suites=["LongMemEval"],
        checked_at=checked_at,
    )

    musique = next(item for item in contract["suite_activation"] if item["suite_name"] == "MuSiQue")
    long_memory = next(item for item in contract["suite_activation"] if item["suite_name"] == "LongMemEval")
    locomo = next(item for item in contract["suite_activation"] if item["suite_name"] == "LoCoMo")

    assert musique["status"] == "pending_publication"
    assert musique["freshness_status"] == "fresh"
    assert musique["published_modes"] == ["semantic_only"]
    assert long_memory["status"] == "reference_only"
    assert long_memory["freshness_status"] == "unpublished"
    assert locomo["status"] == "planned"


def test_phase1_activation_marks_long_memory_suite_active_when_all_modes_are_present() -> None:
    artifacts = [
        _phase1_artifact(suite_id="LongMemEval", mode="semantic_only"),
        _phase1_artifact(suite_id="LongMemEval", mode="coordinate_guided"),
        _phase1_artifact(suite_id="LongMemEval", mode="full_dss"),
    ]

    checked_at = datetime(2026, 4, 26, 13, 0, tzinfo=timezone.utc)
    contract = build_phase1_activation_contract(artifacts, checked_at=checked_at)
    long_memory = next(item for item in contract["suite_activation"] if item["suite_name"] == "LongMemEval")

    assert long_memory["family"] == "long_memory"
    assert long_memory["status"] == "active"
    assert long_memory["freshness_status"] == "fresh"
    assert long_memory["published_modes"] == ["coordinate_guided", "full_dss", "semantic_only"]


def test_phase1_activation_marks_suite_stale_when_latest_run_is_too_old() -> None:
    artifacts = [
        _phase1_artifact(suite_id="HotpotQA", mode="semantic_only"),
        _phase1_artifact(suite_id="HotpotQA", mode="coordinate_guided"),
        _phase1_artifact(suite_id="HotpotQA", mode="full_dss"),
    ]
    for artifact in artifacts:
        artifact["executed_at"] = "2026-04-20T12:00:00Z"

    checked_at = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
    contract = build_phase1_activation_contract(
        artifacts,
        checked_at=checked_at,
        max_age_hours=24,
    )
    hotpot = next(item for item in contract["suite_activation"] if item["suite_name"] == "HotpotQA")

    assert hotpot["status"] == "active"
    assert hotpot["freshness_status"] == "stale"
    assert hotpot["age_hours"] is not None
