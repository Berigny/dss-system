from __future__ import annotations

from backend.benchmarks.operator_publication import (
    OPERATOR_REQUIRED_JOB_STATES,
    OPERATOR_TRIGGER_LABEL,
    OPERATOR_TRIGGER_MODES,
    build_operator_publication_contract,
)


def test_operator_publication_contract_freezes_owner_boundary_and_request_path_policy() -> None:
    contract = build_operator_publication_contract()

    assert contract["owner_surface"] == "dss_dashboard"
    assert contract["execution_owner"] == "ds_backend_local"
    assert contract["request_path_policy"] == "background_jobs_only"
    assert contract["trigger_label"] == OPERATOR_TRIGGER_LABEL
    assert contract["trigger_modes"] == list(OPERATOR_TRIGGER_MODES)
    assert "inline_page_execution" in contract["non_goals"]
    assert "ad_hoc_manual_copy_only_refresh" in contract["non_goals"]


def test_operator_publication_contract_freezes_domain_groups_and_execution_rules() -> None:
    contract = build_operator_publication_contract()
    domains = {item["domain_key"]: item for item in contract["domain_groups"]}

    assert set(domains) == {"retrieval", "memory_traceability", "governance", "latency_and_cost"}
    assert domains["retrieval"]["suite_names"] == ["MuSiQue", "HotpotQA", "2WikiMultiHopQA"]
    assert domains["retrieval"]["trigger_mode"] == "execute_and_publish"
    assert domains["retrieval"]["domain_semantics"] == "execute_phase1_retrieval_suites_then_publish"
    assert domains["memory_traceability"]["suite_names"] == ["LongMemEval", "LoCoMo"]
    assert domains["memory_traceability"]["trigger_mode"] == "execute_and_publish"
    assert domains["governance"]["execution_rule"] == "run_dss_native_governance_suite"
    assert domains["governance"]["trigger_mode"] == "publish_existing"
    assert "planned_or_publish_existing" in domains["governance"]["domain_semantics"]
    assert domains["latency_and_cost"]["execution_rule"] == "derive_from_domain_runs"
    assert domains["latency_and_cost"]["trigger_mode"] == "publish_existing"


def test_operator_publication_contract_freezes_job_state_permission_and_completion_requirements() -> None:
    contract = build_operator_publication_contract()

    assert contract["required_job_states"] == list(OPERATOR_REQUIRED_JOB_STATES)
    assert contract["permission_requirements"] == ["explicit_operator_or_admin_authority"]
    assert contract["completion_requirements"] == [
        "regenerate_canonical_publication",
        "refresh_activation_and_freshness_state",
    ]
    assert contract["required_job_states"] == [
        "queued",
        "running_benchmarks",
        "writing_artefacts",
        "publishing",
        "failed",
        "published",
    ]
