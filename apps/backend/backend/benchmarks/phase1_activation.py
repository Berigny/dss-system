"""Phase 1 industry benchmark activation contract for Epic 5 hardening."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Mapping, Sequence

from backend.benchmarks.artifact_schema import BenchmarkArtifact, BenchmarkMode, validate_benchmark_artifact


Phase1SuiteName = Literal["MuSiQue", "HotpotQA", "2WikiMultiHopQA", "LongMemEval", "LoCoMo", "RULER256K"]
Phase1ActivationStatus = Literal["active", "pending_publication", "reference_only", "planned"]
Phase1FreshnessStatus = Literal["fresh", "stale", "unpublished"]

PHASE1_REQUIRED_MODES: tuple[BenchmarkMode, ...] = (
    "semantic_only",
    "coordinate_guided",
    "full_dss",
)

PHASE1_REQUIRED_METRICS: tuple[str, ...] = (
    "recall_at_1",
    "recall_at_5",
    "avg_latency_ms",
    "token_cost",
)


@dataclass(frozen=True)
class Phase1SuiteContract:
    suite_name: Phase1SuiteName
    family: Literal["retrieval_and_multihop", "long_memory"]


PHASE1_SUITE_CONTRACTS: tuple[Phase1SuiteContract, ...] = (
    Phase1SuiteContract("MuSiQue", "retrieval_and_multihop"),
    Phase1SuiteContract("HotpotQA", "retrieval_and_multihop"),
    Phase1SuiteContract("2WikiMultiHopQA", "retrieval_and_multihop"),
    Phase1SuiteContract("LongMemEval", "long_memory"),
    Phase1SuiteContract("LoCoMo", "long_memory"),
    Phase1SuiteContract("RULER256K", "long_memory"),
)

_PHASE1_SUITE_NAMES = {item.suite_name for item in PHASE1_SUITE_CONTRACTS}


def _artifact_from_any(item: BenchmarkArtifact | Mapping[str, Any]) -> BenchmarkArtifact:
    if isinstance(item, BenchmarkArtifact):
        return item
    allowed = BenchmarkArtifact.model_fields.keys()
    filtered = {key: value for key, value in dict(item).items() if key in allowed}
    return validate_benchmark_artifact(filtered)


def _artifact_has_required_phase1_metrics(artifact: BenchmarkArtifact) -> bool:
    retrieval_group = artifact.metrics.get("retrieval")
    latency_group = artifact.metrics.get("latency")
    cost_group = artifact.metrics.get("cost")
    if retrieval_group is None or retrieval_group.status != "present":
        return False
    if latency_group is None or latency_group.status != "present":
        return False
    if cost_group is None or cost_group.status != "present":
        return False
    retrieval_metrics = set(retrieval_group.metrics.keys())
    latency_metrics = set(latency_group.metrics.keys())
    cost_metrics = set(cost_group.metrics.keys())
    return (
        "recall_at_1" in retrieval_metrics
        and "recall_at_5" in retrieval_metrics
        and "avg_latency_ms" in latency_metrics
        and "token_cost" in cost_metrics
    )


def phase1_suite_activation_statuses(
    artifacts: Iterable[BenchmarkArtifact | Mapping[str, Any]],
    *,
    reference_only_suites: Sequence[str] | None = None,
    checked_at: datetime | None = None,
    max_age_hours: int = 168,
) -> list[dict[str, Any]]:
    validated = [_artifact_from_any(item) for item in artifacts]
    observed_at = checked_at or datetime.now(timezone.utc)
    reference_only = {name for name in (reference_only_suites or ()) if name in _PHASE1_SUITE_NAMES}
    suite_runs: dict[str, list[BenchmarkArtifact]] = {name: [] for name in _PHASE1_SUITE_NAMES}
    for artifact in validated:
        if artifact.suite_id in suite_runs:
            suite_runs[artifact.suite_id].append(artifact)

    statuses: list[dict[str, Any]] = []
    for contract in PHASE1_SUITE_CONTRACTS:
        runs = suite_runs[contract.suite_name]
        published_modes = sorted({run.mode for run in runs if _artifact_has_required_phase1_metrics(run)})
        latest_run = max(runs, key=lambda run: run.executed_at) if runs else None
        latest_executed_at = latest_run.executed_at if latest_run is not None else None
        age_hours = (
            round((observed_at - latest_executed_at).total_seconds() / 3600.0, 3)
            if latest_executed_at is not None
            else None
        )
        if len(published_modes) == len(PHASE1_REQUIRED_MODES):
            status: Phase1ActivationStatus = "active"
        elif runs:
            status = "pending_publication"
        elif contract.suite_name in reference_only:
            status = "reference_only"
        else:
            status = "planned"
        freshness_status: Phase1FreshnessStatus
        if latest_run is None:
            freshness_status = "unpublished"
        elif age_hours is not None and age_hours <= float(max_age_hours):
            freshness_status = "fresh"
        else:
            freshness_status = "stale"
        statuses.append(
            {
                "suite_name": contract.suite_name,
                "family": contract.family,
                "status": status,
                "freshness_status": freshness_status,
                "checked_at": observed_at.isoformat(),
                "max_age_hours": max_age_hours,
                "latest_run_id": latest_run.run_id if latest_run is not None else "",
                "latest_executed_at": latest_executed_at.isoformat() if latest_executed_at is not None else "",
                "age_hours": age_hours,
                "required_modes": list(PHASE1_REQUIRED_MODES),
                "published_modes": published_modes,
                "required_metrics": list(PHASE1_REQUIRED_METRICS),
                "run_count": len(runs),
            }
        )
    return statuses


def build_phase1_activation_contract(
    artifacts: Iterable[BenchmarkArtifact | Mapping[str, Any]],
    *,
    reference_only_suites: Sequence[str] | None = None,
    checked_at: datetime | None = None,
    max_age_hours: int = 168,
) -> dict[str, Any]:
    observed_at = checked_at or datetime.now(timezone.utc)
    return {
        "phase": "phase_1",
        "publication_checked_at": observed_at.isoformat(),
        "max_age_hours": max_age_hours,
        "suite_families": {
            "retrieval_and_multihop": ["MuSiQue", "HotpotQA", "2WikiMultiHopQA"],
            "long_memory": ["LongMemEval", "LoCoMo", "RULER256K"],
        },
        "required_modes": list(PHASE1_REQUIRED_MODES),
        "required_metrics": list(PHASE1_REQUIRED_METRICS),
        "suite_activation": phase1_suite_activation_statuses(
            artifacts,
            reference_only_suites=reference_only_suites,
            checked_at=observed_at,
            max_age_hours=max_age_hours,
        ),
    }
