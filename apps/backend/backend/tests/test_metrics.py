from __future__ import annotations

from datetime import datetime, timezone

import pytest
from starlette.requests import Request

from backend.api.stats import (
    TelemetryEventRequest,
    _calculate_metrics_from_events,
    _calculate_metrics_from_rollup,
    _rollup_has_signal,
    _session_namespace_candidates,
)
from backend.metrics.telemetry import (
    TelemetryIds,
    TelemetryReferences,
    TelemetrySearchFlags,
    TurnTelemetry,
)
from backend.fieldx_kernel.kernel_origin_equations import calculate_alpha_from_primes
from backend.fieldx_kernel.metrics import (
    CODATA_ALPHA_INV,
    compute_delta_sub,
    correlate_residual_to_sim_metrics,
)


def _build_turn(
    *,
    emitted_refs: int = 0,
    resolve_attempts: int = 0,
    resolve_successes: int = 0,
    search_requested: bool | None = None,
    search_used: bool | None = None,
    memory_cost: float | None = None,
    ingest_words: int | None = None,
    memory_tokens: int | None = None,
    quarantine_write: bool | None = None,
    quarantine_reason: str | None = None,
    authz_denied: bool | None = None,
    authz_reason: str | None = None,
    authz_principal_source: str | None = None,
    authz_principal_mode: str | None = None,
    auth_error_class: str | None = None,
    auth_token_validation_failed: bool | None = None,
    eq9_eval_source: str | None = None,
    meta_patch_status: str | None = None,
    meta_patch_reason: str | None = None,
    model: str | None = None,
    provider: str | None = None,
) -> TurnTelemetry:
    return TurnTelemetry(
        ids=TelemetryIds(
            session_id="session-1",
            namespace="chat-session-1",
            turn_id="turn-1",
            timestamp=datetime.now(timezone.utc),
        ),
        references=TelemetryReferences(
            emitted_refs=emitted_refs,
            resolve_attempts=resolve_attempts,
            resolve_successes=resolve_successes,
        ),
        search=TelemetrySearchFlags(
            requested=search_requested,
            used=search_used,
        ),
        memory_cost=memory_cost,
        ingest_words=ingest_words,
        memory_tokens=memory_tokens,
        quarantine_write=quarantine_write,
        quarantine_reason=quarantine_reason,
        authz_denied=authz_denied,
        authz_reason=authz_reason,
        authz_principal_source=authz_principal_source,
        authz_principal_mode=authz_principal_mode,
        auth_error_class=auth_error_class,
        auth_token_validation_failed=auth_token_validation_failed,
        eq9_eval_source=eq9_eval_source,
        meta_patch_status=meta_patch_status,
        meta_patch_reason=meta_patch_reason,
        model=model,
        provider=provider,
    )


def _turn_payload(**kwargs) -> dict:
    return _build_turn(**kwargs).model_dump(mode="json")


def test_metrics_from_events_rates() -> None:
    events = [
        _turn_payload(
            emitted_refs=2,
            resolve_attempts=2,
            resolve_successes=1,
            search_requested=True,
            search_used=False,
            memory_cost=2.0,
            ingest_words=1000,
            memory_tokens=4000,
        ),
        _turn_payload(
            emitted_refs=1,
            resolve_attempts=1,
            resolve_successes=1,
            search_requested=True,
            search_used=True,
            memory_cost=1.0,
            ingest_words=2000,
            memory_tokens=2000,
        ),
        _turn_payload(
            emitted_refs=0,
            resolve_attempts=0,
            resolve_successes=0,
            search_requested=False,
            search_used=False,
            memory_cost=3.0,
            ingest_words=0,
            memory_tokens=0,
        ),
    ]

    payload = _calculate_metrics_from_events(events)

    assert payload["metrics"]["verifiable_response_rate"] == 2 / 3
    assert payload["metrics"]["resolve_success_rate"] == 2 / 3
    assert payload["metrics"]["search_avoided_rate"] == 0.5
    assert payload["metrics"]["memory_cost_per_10k_words"] == 12.5
    assert payload["metrics"]["memory_cost_per_1m_tokens"] == 500.0


def test_metrics_from_events_edge_cases() -> None:
    missing_refs_event = _turn_payload(
        search_requested=None,
        search_used=None,
        memory_cost=4.0,
        ingest_words=0,
        memory_tokens=0,
    )
    missing_refs_event.pop("references", None)

    payload = _calculate_metrics_from_events([missing_refs_event])

    assert payload["metrics"]["verifiable_response_rate"] == 0.0
    assert payload["metrics"]["resolve_success_rate"] == 0.0
    assert payload["metrics"]["search_avoided_rate"] == 0.0
    assert payload["metrics"]["memory_cost_per_10k_words"] == 0.0
    assert payload["metrics"]["memory_cost_per_1m_tokens"] == 0.0


def test_metrics_from_rollup_rates_and_edge_cases() -> None:
    rollup = {
        "events": 3,
        "emitted_refs": 4,
        "resolve_attempts": 2,
        "resolve_successes": 1,
        "search_requested": 0,
        "search_used": 0,
        "memory_cost": 5.0,
        "ingest_words": 2000,
        "memory_tokens": 5000,
    }

    payload = _calculate_metrics_from_rollup(rollup)

    assert payload["metrics"]["verifiable_response_rate"] == 0.25
    assert payload["metrics"]["resolve_success_rate"] == 0.5
    assert payload["metrics"]["search_avoided_rate"] == 0.0
    assert payload["metrics"]["memory_cost_per_10k_words"] == 25.0
    assert payload["metrics"]["memory_cost_per_1m_tokens"] == 1000.0
    assert payload["metrics_coverage"]["search_invariant_repairs"] == 0
    assert payload["alerts"]["search_invariant_repair_active"] is False

    zero_denominator_payload = _calculate_metrics_from_rollup({})

    assert zero_denominator_payload["metrics"]["verifiable_response_rate"] == 0.0
    assert zero_denominator_payload["metrics"]["resolve_success_rate"] == 0.0
    assert zero_denominator_payload["metrics"]["search_avoided_rate"] == 0.0
    assert zero_denominator_payload["metrics"]["memory_cost_per_10k_words"] == 0.0
    assert zero_denominator_payload["metrics"]["memory_cost_per_1m_tokens"] == 0.0


def test_metrics_from_rollup_clamps_inconsistent_search_counts() -> None:
    rollup = {
        "events": 2,
        "search_requested": 1,
        "search_used": 3,
    }
    payload = _calculate_metrics_from_rollup(rollup)
    assert payload["totals"]["search_requested"] == 1
    assert payload["totals"]["search_used"] == 1
    assert payload["totals"]["search_invariant_repairs"] == 2
    assert payload["metrics"]["search_avoided_rate"] == 0.0
    assert payload["alerts"]["search_invariant_repair_active"] is True


def test_metrics_from_events_clamps_inconsistent_search_flags() -> None:
    events = [
        _turn_payload(search_requested=False, search_used=True),
        _turn_payload(search_requested=True, search_used=True),
    ]
    payload = _calculate_metrics_from_events(events)
    assert payload["totals"]["search_requested"] == 1
    assert payload["totals"]["search_used"] == 1
    assert payload["totals"]["search_invariant_repairs"] == 1
    assert payload["alerts"]["search_invariant_repair_active"] is True


def test_metrics_from_events_tracks_quarantine_writes() -> None:
    events = [
        _turn_payload(quarantine_write=True, quarantine_reason="loop_blocked", model="m1"),
        _turn_payload(quarantine_write=True, quarantine_reason="audit_blocked", model="m1"),
        _turn_payload(quarantine_write=True, quarantine_reason="persistence_error", model="m1"),
        _turn_payload(quarantine_write=False, model="m1"),
    ]
    payload = _calculate_metrics_from_events(events)
    assert payload["totals"]["chat_turns"] == 4
    assert payload["totals"]["quarantine_writes"] == 3
    assert payload["totals"]["quarantine_loop_blocked"] == 1
    assert payload["totals"]["quarantine_audit_blocked"] == 1
    assert payload["totals"]["quarantine_persistence_error"] == 1
    assert payload["metrics"]["quarantine_write_rate"] == 0.75
    assert payload["alerts"]["quarantine_write_alert_active"] is True
    assert payload["alerts"]["quarantine_dominant_reason"] in {
        "loop_blocked",
        "audit_blocked",
        "persistence_error",
    }
    assert payload["alerts"]["quarantine_reason_breakdown"]["loop_blocked"] == 1


def test_metrics_from_rollup_tracks_quarantine_writes() -> None:
    rollup = {
        "chat_turns": 10,
        "quarantine_writes": 2,
        "quarantine_loop_blocked": 1,
        "quarantine_audit_blocked": 1,
        "quarantine_persistence_error": 0,
    }
    payload = _calculate_metrics_from_rollup(rollup)
    assert payload["totals"]["quarantine_writes"] == 2
    assert payload["totals"]["quarantine_loop_blocked"] == 1
    assert payload["totals"]["quarantine_audit_blocked"] == 1
    assert payload["totals"]["quarantine_persistence_error"] == 0
    assert payload["metrics"]["quarantine_write_rate"] == 0.2
    assert payload["alerts"]["quarantine_write_alert_active"] is True
    assert payload["alerts"]["quarantine_dominant_reason"] in {"loop_blocked", "audit_blocked"}


def test_metrics_from_events_tracks_auth_observability_rollup() -> None:
    events = [
        _turn_payload(
            model="m1",
            authz_denied=True,
            authz_reason="did_principal_required",
            authz_principal_source="legacy_header",
            authz_principal_mode="compat",
            auth_error_class="token_validation_failed",
        ),
        _turn_payload(
            model="m1",
            authz_denied=False,
            authz_reason="write_privilege_granted",
            authz_principal_source="did_header",
            authz_principal_mode="did_strict",
        ),
    ]
    payload = _calculate_metrics_from_events(events)
    totals = payload["totals"]
    metrics = payload["metrics"]
    alerts = payload["alerts"]

    assert totals["authz_decisions"] == 2
    assert totals["authz_denied"] == 1
    assert totals["authz_allowed"] == 1
    assert totals["authz_reason_did_principal_required"] == 1
    assert totals["authz_reason_other"] == 1
    assert totals["auth_principal_source_legacy_header"] == 1
    assert totals["auth_principal_source_did_header"] == 1
    assert totals["auth_error_class_token_validation_failed"] == 1
    assert totals["auth_token_validation_failures"] == 1
    assert metrics["authz_deny_rate"] == 0.5
    assert metrics["did_principal_usage_rate"] == 0.5
    assert metrics["legacy_principal_usage_rate"] == 0.5
    assert metrics["auth_token_validation_failure_rate"] == 0.5
    assert alerts["authz_deny_spike_active"] is False
    assert alerts["auth_token_validation_failure_active"] is True


def test_metrics_from_rollup_tracks_auth_observability_rollup() -> None:
    rollup = {
        "authz_decisions": 20,
        "authz_denied": 8,
        "authz_allowed": 12,
        "authz_reason_did_principal_required": 3,
        "authz_reason_context_not_allowed": 1,
        "authz_reason_write_requires_owner_or_tenant": 2,
        "authz_reason_read_requires_owner_or_tenant": 1,
        "authz_reason_admin_principal_required": 1,
        "authz_reason_unknown_ledger": 0,
        "authz_reason_other": 0,
        "auth_principal_source_legacy_header": 9,
        "auth_principal_source_did_header": 11,
        "auth_principal_source_other": 0,
        "auth_principal_mode_compat": 10,
        "auth_principal_mode_did_strict": 10,
        "auth_error_class_token_validation_failed": 4,
        "auth_error_class_other": 1,
        "auth_token_validation_failures": 4,
    }
    payload = _calculate_metrics_from_rollup(rollup)
    totals = payload["totals"]
    metrics = payload["metrics"]
    alerts = payload["alerts"]

    assert totals["authz_decisions"] == 20
    assert totals["authz_denied"] == 8
    assert totals["auth_principal_source_did_header"] == 11
    assert totals["auth_error_class_token_validation_failed"] == 4
    assert metrics["authz_deny_rate"] == 0.4
    assert metrics["did_principal_usage_rate"] == pytest.approx(11 / 20)
    assert metrics["legacy_principal_usage_rate"] == pytest.approx(9 / 20)
    assert metrics["auth_token_validation_failure_rate"] == pytest.approx(4 / 20)
    assert alerts["authz_deny_spike_active"] is True
    assert alerts["auth_token_validation_failure_active"] is True


def test_metrics_from_events_tracks_eq9_source_and_meta_patch_rollup() -> None:
    events = [
        _turn_payload(
            model="m1",
            eq9_eval_source="post_commit_metadata",
            meta_patch_status="applied",
        ),
        _turn_payload(
            model="m1",
            eq9_eval_source="post_commit_cache",
            meta_patch_status="skipped",
            meta_patch_reason="post_introspect_timeout",
        ),
        _turn_payload(
            model="m1",
            eq9_eval_source="post_commit_introspect",
            meta_patch_status="skipped",
            meta_patch_reason="post_introspect_error",
        ),
    ]

    payload = _calculate_metrics_from_events(events)
    totals = payload["totals"]
    metrics = payload["metrics"]
    coverage = payload["metrics_coverage"]

    assert totals["eq9_eval_source_post_commit_metadata"] == 1
    assert totals["eq9_eval_source_post_commit_cache"] == 1
    assert totals["eq9_eval_source_post_commit_introspect"] == 1
    assert totals["meta_patch_applied"] == 1
    assert totals["meta_patch_skipped"] == 2
    assert totals["meta_patch_timeout"] == 1
    assert totals["meta_patch_error"] == 1
    assert totals["meta_patch_other_skip"] == 0
    assert metrics["meta_patch_applied_rate"] == pytest.approx(1 / 3)
    assert metrics["meta_patch_timeout_rate"] == pytest.approx(1 / 3)
    assert metrics["meta_patch_error_rate"] == pytest.approx(1 / 3)
    assert coverage["eq9_eval_source_samples"] == 3
    assert coverage["meta_patch_samples"] == 3


def test_metrics_from_rollup_tracks_eq9_source_and_meta_patch_rollup() -> None:
    rollup = {
        "eq9_eval_source_post_commit_metadata": 4,
        "eq9_eval_source_post_commit_cache": 3,
        "eq9_eval_source_post_commit_introspect": 2,
        "meta_patch_applied": 5,
        "meta_patch_skipped": 5,
        "meta_patch_timeout": 3,
        "meta_patch_error": 1,
        "meta_patch_other_skip": 1,
    }
    payload = _calculate_metrics_from_rollup(rollup)
    totals = payload["totals"]
    metrics = payload["metrics"]
    coverage = payload["metrics_coverage"]

    assert totals["eq9_eval_source_post_commit_metadata"] == 4
    assert totals["eq9_eval_source_post_commit_cache"] == 3
    assert totals["eq9_eval_source_post_commit_introspect"] == 2
    assert totals["meta_patch_applied"] == 5
    assert totals["meta_patch_skipped"] == 5
    assert totals["meta_patch_timeout"] == 3
    assert totals["meta_patch_error"] == 1
    assert totals["meta_patch_other_skip"] == 1
    assert metrics["meta_patch_applied_rate"] == 0.5
    assert metrics["meta_patch_timeout_rate"] == 0.3
    assert metrics["meta_patch_error_rate"] == 0.1
    assert coverage["eq9_eval_source_samples"] == 9
    assert coverage["meta_patch_samples"] == 10


def test_delta_sub_paper_value() -> None:
    alpha = calculate_alpha_from_primes()
    expected_delta = CODATA_ALPHA_INV - (1.0 / alpha)
    assert compute_delta_sub(alpha) == pytest.approx(expected_delta, rel=1e-10)


def test_correlate_clamping() -> None:
    metrics = correlate_residual_to_sim_metrics(
        calculate_alpha_from_primes(),
        avg_coherence_drift=10.0,
        flow_violation_rate=10.0,
        drift_scale=0.1,
        violation_scale=0.01,
    )
    assert metrics["unexplained_ppm"] == 0.0


def test_delta_zero_error() -> None:
    with pytest.raises(ValueError):
        compute_delta_sub(0.0)


def test_telemetry_event_request_hides_gen_cost_but_accepts_legacy_input() -> None:
    payload = TelemetryEventRequest.model_validate(
        {
            "session_id": "s1",
            "gen_cost": 1.25,
        }
    )
    assert isinstance(payload.model_extra, dict)
    assert payload.model_extra.get("gen_cost") == 1.25
    schema = TelemetryEventRequest.model_json_schema()
    assert "gen_cost" not in schema.get("properties", {})


def test_session_namespace_candidates_prioritize_header_scope() -> None:
    request = Request(
        scope={
            "type": "http",
            "method": "GET",
            "path": "/stats/session",
            "headers": [(b"x-ledger-id", b"chat-demo")],
        }
    )
    candidates = _session_namespace_candidates(request, "web-mm92cjpl")
    assert candidates[0] == "chat-demo"
    assert "web-mm92cjpl" in candidates
    assert "chat-web-mm92cjpl" in candidates


def test_rollup_has_signal_detects_non_zero_metrics() -> None:
    assert _rollup_has_signal({}) is False
    assert _rollup_has_signal({"events": 0, "cost": 0.0}) is False
    assert _rollup_has_signal({"events": 1, "cost": 0.0}) is True
