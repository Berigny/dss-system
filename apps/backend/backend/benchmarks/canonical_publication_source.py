"""Canonical benchmark publication source contract for Epic 5."""

from __future__ import annotations

from typing import Any


CANONICAL_PUBLICATION_OWNER = "ds_backend_local"
CONTROL_PLANE_CONSUMER = "dss_dashboard"
PRODUCTION_TRUTH_RULE = "dashboard_must_consume_backend_publication_truth"

REQUIRED_BACKEND_CONFIG: tuple[str, ...] = (
    "BENCHMARK_ARTIFACT_ROOT",
    "BENCHMARK_PUBLICATION_OUTPUT",
)

REQUIRED_FAILURE_STATES: tuple[str, ...] = (
    "benchmark_artifact_root_not_configured",
    "benchmark_publication_output_not_configured",
    "no_valid_benchmark_artifacts_found",
    "canonical_publication_unavailable",
)

REQUIRED_OUTCOMES: tuple[str, ...] = (
    "backend_owns_canonical_publication_feed",
    "control_plane_api_reads_backend_backed_publication",
    "prod_rendering_and_prod_publication_share_one_truth_source",
)


def build_canonical_publication_source_contract() -> dict[str, Any]:
    return {
        "canonical_publication_owner": CANONICAL_PUBLICATION_OWNER,
        "control_plane_consumer": CONTROL_PLANE_CONSUMER,
        "production_truth_rule": PRODUCTION_TRUTH_RULE,
        "required_backend_config": list(REQUIRED_BACKEND_CONFIG),
        "required_failure_states": list(REQUIRED_FAILURE_STATES),
        "required_outcomes": list(REQUIRED_OUTCOMES),
    }
