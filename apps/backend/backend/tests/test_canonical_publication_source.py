from backend.benchmarks.canonical_publication_source import (
    build_canonical_publication_source_contract,
)


def test_canonical_publication_source_contract_freezes_owner_and_consumer_boundary() -> None:
    contract = build_canonical_publication_source_contract()

    assert contract["canonical_publication_owner"] == "ds_backend_local"
    assert contract["control_plane_consumer"] == "dss_dashboard"
    assert contract["production_truth_rule"] == "dashboard_must_consume_backend_publication_truth"


def test_canonical_publication_source_contract_freezes_required_config_failure_states_and_outcomes() -> None:
    contract = build_canonical_publication_source_contract()

    assert contract["required_backend_config"] == [
        "BENCHMARK_ARTIFACT_ROOT",
        "BENCHMARK_PUBLICATION_OUTPUT",
    ]
    assert contract["required_failure_states"] == [
        "benchmark_artifact_root_not_configured",
        "benchmark_publication_output_not_configured",
        "no_valid_benchmark_artifacts_found",
        "canonical_publication_unavailable",
    ]
    assert contract["required_outcomes"] == [
        "backend_owns_canonical_publication_feed",
        "control_plane_api_reads_backend_backed_publication",
        "prod_rendering_and_prod_publication_share_one_truth_source",
    ]
