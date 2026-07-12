"""Telemetry metrics package."""

from .benchmark_context import attach_request_benchmark_context
from .prod_benchmark_contract import (
    AlwaysOnBenchmarkSignal,
    BenchmarkCorrelationIds,
    ExportPolicy,
    ProductionBenchmarkTelemetryContract,
    SampledBenchmarkTrace,
    default_production_benchmark_contract,
)

__all__ = [
    "AlwaysOnBenchmarkSignal",
    "BenchmarkCorrelationIds",
    "ExportPolicy",
    "ProductionBenchmarkTelemetryContract",
    "SampledBenchmarkTrace",
    "attach_request_benchmark_context",
    "default_production_benchmark_contract",
]
