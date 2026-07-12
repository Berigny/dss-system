"""Canonical low-overhead production telemetry contract for benchmark signals."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SurfaceName(str, Enum):
    BACKEND = "backend"
    MIDDLEWARE = "middleware"
    CHAT = "chat"
    CONTROL_PLANE = "control_plane"
    DECODER = "decoder"


class TelemetryTier(str, Enum):
    ALWAYS_ON = "always_on"
    SAMPLED_TRACE = "sampled_trace"


class ExportTransport(str, Enum):
    ASYNC_BUFFER = "async_buffer"
    SIDECAR_BATCH = "sidecar_batch"
    UDP_STATSD = "udp_statsd"


class BackpressureMode(str, Enum):
    DROP_NEWEST = "drop_newest"
    DROP_OLDEST = "drop_oldest"
    SAMPLE_DOWN = "sample_down"


class BenchmarkCorrelationIds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    principal_id: str | None = None
    principal_hash: str | None = None
    tenant_id: str = Field(min_length=1)
    surface: SurfaceName
    mode: str = Field(min_length=1)
    build_sha: str = Field(min_length=7)

    @model_validator(mode="after")
    def validate_principal_identity(self) -> "BenchmarkCorrelationIds":
        if not (self.principal_id or self.principal_hash):
            raise ValueError("principal_id or principal_hash is required")
        return self


class AlwaysOnBenchmarkSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier: Literal[TelemetryTier.ALWAYS_ON] = TelemetryTier.ALWAYS_ON
    correlation: BenchmarkCorrelationIds
    latency_ms: float = Field(ge=0)
    success: bool
    retrieval_count: int | None = Field(default=None, ge=0)
    coordinate_count: int | None = Field(default=None, ge=0)
    policy_outcome: str | None = None
    error_class: str | None = None
    sampled: bool = False


class SampledBenchmarkTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier: Literal[TelemetryTier.SAMPLED_TRACE] = TelemetryTier.SAMPLED_TRACE
    correlation: BenchmarkCorrelationIds
    retrieved_coordinates: list[str] = Field(default_factory=list)
    walk_path: list[str] = Field(default_factory=list)
    replay_outcome: str | None = None
    exemplar_payload_ref: str | None = None
    sampling_rate: float = Field(gt=0, le=1)
    trigger_reason: str = Field(min_length=1)


class ExportPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transports: list[ExportTransport] = Field(min_length=1)
    non_blocking: bool = True
    bounded_payload_bytes: int = Field(default=2048, ge=128)
    buffer_capacity: int = Field(default=10_000, ge=1)
    backpressure_mode: BackpressureMode = BackpressureMode.DROP_NEWEST
    export_timeout_ms: int = Field(default=50, ge=1)
    drop_counter_required: bool = True
    exporter_lag_metric_required: bool = True


class ProductionBenchmarkTelemetryContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "v1"
    required_surfaces: list[SurfaceName] = Field(
        default_factory=lambda: [
            SurfaceName.BACKEND,
            SurfaceName.MIDDLEWARE,
            SurfaceName.CHAT,
            SurfaceName.CONTROL_PLANE,
        ]
    )
    always_on_fields: list[str] = Field(
        default_factory=lambda: [
            "request_id",
            "session_id",
            "principal_id_or_hash",
            "tenant_id",
            "surface",
            "mode",
            "build_sha",
            "latency_ms",
            "success",
            "retrieval_count",
            "coordinate_count",
            "policy_outcome",
            "error_class",
        ]
    )
    sampled_trace_fields: list[str] = Field(
        default_factory=lambda: [
            "retrieved_coordinates",
            "walk_path",
            "replay_outcome",
            "exemplar_payload_ref",
            "sampling_rate",
            "trigger_reason",
        ]
    )
    always_on_sampling_rate: float = Field(default=1.0, ge=1.0, le=1.0)
    failure_sampling_rate: float = Field(default=1.0, ge=0, le=1.0)
    rich_trace_sampling_rate: float = Field(default=0.1, gt=0, le=1.0)
    export_policy: ExportPolicy = Field(
        default_factory=lambda: ExportPolicy(
            transports=[ExportTransport.ASYNC_BUFFER, ExportTransport.SIDECAR_BATCH]
        )
    )
    shadow_replay_recommended: bool = True
    synchronous_remote_writes_allowed: bool = False
    synchronous_benchmark_publication_allowed: bool = False


def default_production_benchmark_contract() -> ProductionBenchmarkTelemetryContract:
    """Return the canonical low-overhead prod telemetry contract."""

    return ProductionBenchmarkTelemetryContract()
