from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.metrics.prod_benchmark_contract import (
    AlwaysOnBenchmarkSignal,
    BackpressureMode,
    BenchmarkCorrelationIds,
    ExportTransport,
    ProductionBenchmarkTelemetryContract,
    SampledBenchmarkTrace,
    SurfaceName,
    default_production_benchmark_contract,
)


def test_default_production_benchmark_contract_is_non_blocking_and_async() -> None:
    contract = default_production_benchmark_contract()

    assert contract.version == "v1"
    assert contract.synchronous_remote_writes_allowed is False
    assert contract.synchronous_benchmark_publication_allowed is False
    assert contract.export_policy.non_blocking is True
    assert ExportTransport.ASYNC_BUFFER in contract.export_policy.transports
    assert ExportTransport.SIDECAR_BATCH in contract.export_policy.transports


def test_correlation_ids_require_principal_identity() -> None:
    with pytest.raises(ValidationError):
        BenchmarkCorrelationIds(
            request_id="req-1",
            session_id="sess-1",
            tenant_id="tenant:demo",
            surface=SurfaceName.BACKEND,
            mode="full_dss",
            build_sha="054b654",
        )


def test_always_on_signal_captures_required_hot_path_fields() -> None:
    signal = AlwaysOnBenchmarkSignal(
        correlation=BenchmarkCorrelationIds(
            request_id="req-1",
            session_id="sess-1",
            principal_hash="sha256:abc",
            tenant_id="tenant:demo",
            surface=SurfaceName.CHAT,
            mode="full_dss",
            build_sha="054b654",
        ),
        latency_ms=18.4,
        success=True,
        retrieval_count=4,
        coordinate_count=2,
        policy_outcome="allow",
    )

    assert signal.tier == "always_on"
    assert signal.sampled is False
    assert signal.correlation.surface == SurfaceName.CHAT


def test_sampled_trace_distinguishes_rich_payloads_from_hot_path_metrics() -> None:
    trace = SampledBenchmarkTrace(
        correlation=BenchmarkCorrelationIds(
            request_id="req-2",
            session_id="sess-2",
            principal_id="principal:demo",
            tenant_id="tenant:demo",
            surface=SurfaceName.MIDDLEWARE,
            mode="coordinate_guided",
            build_sha="054b654",
        ),
        retrieved_coordinates=["coord:1", "coord:2"],
        walk_path=["coord:1", "coord:2"],
        replay_outcome="resolved",
        exemplar_payload_ref="trace:abc123",
        sampling_rate=0.1,
        trigger_reason="successful_trace_sample",
    )

    assert trace.tier == "sampled_trace"
    assert trace.sampling_rate == 0.1
    assert trace.retrieved_coordinates == ["coord:1", "coord:2"]


def test_export_policy_declares_drop_behavior_and_lag_metrics() -> None:
    contract = ProductionBenchmarkTelemetryContract()

    assert contract.export_policy.backpressure_mode in {
        BackpressureMode.DROP_NEWEST,
        BackpressureMode.DROP_OLDEST,
        BackpressureMode.SAMPLE_DOWN,
    }
    assert contract.export_policy.drop_counter_required is True
    assert contract.export_policy.exporter_lag_metric_required is True
