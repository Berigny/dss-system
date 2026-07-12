"""Canonical benchmark artefact schema for published DSS benchmark runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HardwareProfile(BaseModel):
    """Hardware and accelerator context captured for a benchmark run."""

    platform: str = Field(min_length=1)
    processor: str | None = None
    cpu_count: int | None = Field(default=None, ge=1)
    memory_gb: float | None = Field(default=None, ge=0.0)
    gpu_name: str | None = None
    gpu_count: int | None = Field(default=None, ge=0)
    cuda_version: str | None = None
    mps_available: bool = False
    backend_accelerator: Literal["cuda", "mps", "cpu"] = "cpu"


MetricGroupName = Literal["retrieval", "traceability", "governance", "latency", "cost"]
BenchmarkMode = Literal[
    "semantic_only",
    "coordinate_guided",
    "full_dss",
    "baseline_dense",
    "baseline_hierarchical",
    "baseline_long_context",
    "baseline_grok",
]
BenchmarkStatus = Literal["success", "partial", "failed"]
FreshnessStatus = Literal["fresh", "stale"]

REQUIRED_METRIC_GROUPS: tuple[MetricGroupName, ...] = (
    "retrieval",
    "traceability",
    "governance",
    "latency",
    "cost",
)


class RepoRef(BaseModel):
    name: str = Field(min_length=1)
    commit_sha: str = Field(min_length=7)
    role: str = Field(min_length=1)
    required_for_run: bool = True


class DatasetRef(BaseModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    split: str = Field(min_length=1)
    record_count: int | None = Field(default=None, ge=0)


class MetricStatistics(BaseModel):
    """Sample statistics across multiple random seeds."""

    mean: float
    standard_deviation: float
    min: float
    max: float
    ci_95_low: float
    ci_95_high: float
    sample_count: int = Field(ge=1)


class MetricEntry(BaseModel):
    value: float | int | bool | str
    unit: str | None = None
    description: str | None = None
    statistics: MetricStatistics | None = None


class MetricGroup(BaseModel):
    status: Literal["present", "absent"] = "present"
    absence_reason: str | None = None
    metrics: dict[str, MetricEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_group_state(self) -> "MetricGroup":
        if self.status == "present" and not self.metrics:
            raise ValueError("present metric groups must include at least one metric")
        if self.status == "present" and self.absence_reason:
            raise ValueError("present metric groups cannot include an absence_reason")
        if self.status == "absent" and not self.absence_reason:
            raise ValueError("absent metric groups must include an absence_reason")
        if self.status == "absent" and self.metrics:
            raise ValueError("absent metric groups cannot include metrics")
        return self


class FreshnessInfo(BaseModel):
    status: FreshnessStatus
    checked_at: datetime
    max_age_hours: int = Field(ge=1)
    age_hours: float = Field(ge=0)


class BenchmarkArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artefact_schema_version: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    suite_id: str = Field(min_length=1)
    suite_version: str = Field(min_length=1)
    executed_at: datetime
    mode: BenchmarkMode
    status: BenchmarkStatus
    repos: list[RepoRef] = Field(min_length=1)
    datasets: list[DatasetRef] = Field(min_length=1)
    metrics: dict[MetricGroupName, MetricGroup]
    freshness: FreshnessInfo
    failure_reason: str | None = None
    hardware: HardwareProfile | None = None
    run_config: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_artifact(self) -> "BenchmarkArtifact":
        groups = set(self.metrics.keys())
        missing = [group for group in REQUIRED_METRIC_GROUPS if group not in groups]
        if missing:
            raise ValueError(f"missing required metric groups: {', '.join(missing)}")
        if not any(repo.name == "ds-backend-local" and repo.required_for_run for repo in self.repos):
            raise ValueError("ds-backend-local must be present as required provenance for every run")
        if self.status == "partial":
            absent_groups = [group for group, payload in self.metrics.items() if payload.status == "absent"]
            if not absent_groups:
                raise ValueError("partial artefacts must declare at least one absent metric group")
        if self.status == "success":
            absent_groups = [group for group, payload in self.metrics.items() if payload.status == "absent"]
            if absent_groups:
                raise ValueError("success artefacts cannot contain absent metric groups")
        if self.status == "failed" and not self.failure_reason:
            raise ValueError("failed artefacts must include a failure_reason")
        if self.status != "failed" and self.failure_reason:
            raise ValueError("failure_reason is only valid for failed artefacts")
        return self


def validate_benchmark_artifact(payload: dict[str, Any]) -> BenchmarkArtifact:
    """Validate and return a typed benchmark artefact."""

    return BenchmarkArtifact.model_validate(payload)
