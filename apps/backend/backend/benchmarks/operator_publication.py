"""Operator-triggered benchmark publication contract for Epic 5."""

from __future__ import annotations

from typing import Any, Literal


OperatorDomainKey = Literal["retrieval", "memory_traceability", "governance", "latency_and_cost"]
OperatorTriggerMode = Literal["publish_existing", "execute_and_publish"]
OperatorJobState = Literal["queued", "running_benchmarks", "writing_artefacts", "publishing", "failed", "published"]

OPERATOR_TRIGGER_LABEL = "Update & publish"
OPERATOR_REQUEST_PATH_POLICY = "background_jobs_only"
OPERATOR_PERMISSION_REQUIREMENTS: tuple[str, ...] = ("explicit_operator_or_admin_authority",)
OPERATOR_COMPLETION_REQUIREMENTS: tuple[str, ...] = (
    "regenerate_canonical_publication",
    "refresh_activation_and_freshness_state",
)
OPERATOR_NON_GOALS: tuple[str, ...] = (
    "inline_page_execution",
    "ad_hoc_manual_copy_only_refresh",
)
OPERATOR_REQUIRED_JOB_STATES: tuple[OperatorJobState, ...] = (
    "queued",
    "running_benchmarks",
    "writing_artefacts",
    "publishing",
    "failed",
    "published",
)
OPERATOR_TRIGGER_MODES: tuple[OperatorTriggerMode, ...] = (
    "publish_existing",
    "execute_and_publish",
)

OPERATOR_DOMAIN_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "domain_key": "retrieval",
        "label": "Retrieval",
        "suite_names": ["MuSiQue", "HotpotQA", "2WikiMultiHopQA"],
        "family": "retrieval_and_multihop",
        "execution_rule": "run_phase1_suite_family",
        "trigger_mode": "execute_and_publish",
        "domain_semantics": "execute_phase1_retrieval_suites_then_publish",
    },
    {
        "domain_key": "memory_traceability",
        "label": "Memory / Traceability",
        "suite_names": ["LongMemEval", "LoCoMo"],
        "family": "long_memory",
        "execution_rule": "run_phase1_suite_family",
        "trigger_mode": "execute_and_publish",
        "domain_semantics": "execute_phase1_memory_suites_then_publish",
    },
    {
        "domain_key": "governance",
        "label": "Governance",
        "suite_names": [],
        "family": "dss_native_governance",
        "execution_rule": "run_dss_native_governance_suite",
        "trigger_mode": "publish_existing",
        "domain_semantics": "planned_or_publish_existing_until_dedicated_governance_execution_lane_exists",
    },
    {
        "domain_key": "latency_and_cost",
        "label": "Latency and Cost",
        "suite_names": [],
        "family": "derived_from_benchmark_runs",
        "execution_rule": "derive_from_domain_runs",
        "trigger_mode": "publish_existing",
        "domain_semantics": "derive_from_executed_domain_runs_or_publish_existing_rollups",
    },
)


def build_operator_publication_contract() -> dict[str, Any]:
    return {
        "owner_surface": "dss_dashboard",
        "execution_owner": "ds_backend_local",
        "request_path_policy": OPERATOR_REQUEST_PATH_POLICY,
        "trigger_label": OPERATOR_TRIGGER_LABEL,
        "trigger_modes": list(OPERATOR_TRIGGER_MODES),
        "domain_groups": [dict(item) for item in OPERATOR_DOMAIN_GROUPS],
        "required_job_states": list(OPERATOR_REQUIRED_JOB_STATES),
        "permission_requirements": list(OPERATOR_PERMISSION_REQUIREMENTS),
        "completion_requirements": list(OPERATOR_COMPLETION_REQUIREMENTS),
        "non_goals": list(OPERATOR_NON_GOALS),
    }
