from __future__ import annotations

from backend.services.provenance import _build_gravity_tax_policy, _build_retention_tier


def test_build_retention_tier_defaults_durable_writes_to_clay() -> None:
    payload = _build_retention_tier({"kind": "chat"})
    assert payload["retention_tier"] == "Clay"
    assert payload["retention_tier_reason"] == "durable_ledger_write_path"


def test_build_retention_tier_maps_streaming_multimodal_ingress_to_sand() -> None:
    payload = _build_retention_tier(
        {
            "kind": "audio_stream",
            "input_mode": "audio",
            "streaming": True,
        }
    )
    assert payload["retention_tier"] == "Sand"
    assert payload["retention_tier_reason"] == "high_velocity_multimodal_or_streaming_ingress"


def test_build_retention_tier_maps_working_profile_state_to_silt() -> None:
    payload = _build_retention_tier({"kind": "autonomy_pattern"})
    assert payload["retention_tier"] == "Silt"
    assert payload["retention_tier_reason"] == "active_continuity_or_working_profile_state"


def test_build_gravity_tax_policy_defaults_to_selective_clay_retention() -> None:
    payload = _build_gravity_tax_policy({"kind": "chat"})
    assert payload["gravity_tax_contract_version"] == "gravity-tax-v1"
    assert payload["explicit_retention_cost_policy"] is True
    assert payload["retention_tier"] == "Clay"
    assert payload["retention_tier_reason"] == "durable_ledger_write_path"
    assert payload["retention_tier_assignment"] == "durable_governed_memory_boundary"
    assert payload["gravity_tax_accrual"] == "accruing_durable_governance_cost"
    assert payload["retention_decision_state"] == "durable_keep"
    assert payload["governed_promotion_required"] is True
    assert payload["promotion_state"] == "already_durable"
    assert payload["consolidation_readiness"] == "ready_when_governed_boundary_requests_merge"
    assert payload["anti_hoarding_posture"] == "selective_retention_over_silent_accumulation"
    assert payload["noisy_or_low_coherence_drains_by_default"] is True
    assert payload["cost_inputs"] == {
        "eq4_coupling_live_input": True,
        "eq5_persistence_cost_live_input": True,
    }


def test_build_gravity_tax_policy_preserves_live_cost_inputs_when_present() -> None:
    payload = _build_gravity_tax_policy(
        {
            "kind": "audio_stream",
            "input_mode": "audio",
            "streaming": True,
            "gravity_cost": 1.75,
            "gravity_penalty": 0.42,
        }
    )
    assert payload["retention_tier"] == "Sand"
    assert payload["retention_tier_assignment"] == "high_velocity_ephemeral_ingress"
    assert payload["gravity_tax_accrual"] == "accruing_ephemeral_drain_pressure"
    assert payload["retention_decision_state"] == "evict_or_rolloff_unless_promoted"
    assert payload["governed_promotion_required"] is False
    assert payload["promotion_state"] == "promotion_optional"
    assert payload["consolidation_readiness"] == "not_ready"
    assert payload["gravity_cost"] == 1.75
    assert payload["gravity_penalty"] == 0.42


def test_build_gravity_tax_policy_allows_explicit_retention_telemetry_overrides() -> None:
    payload = _build_gravity_tax_policy(
        {
            "kind": "autonomy_pattern",
            "retention_tier_assignment": "operator_pinned_working_set",
            "gravity_tax_accrual": "manual_review_queue",
            "retention_decision_state": "archive_after_checkpoint",
            "promotion_state": "promotion_deferred",
            "consolidation_readiness": "checkpoint_pending",
        }
    )
    assert payload["retention_tier"] == "Silt"
    assert payload["retention_tier_assignment"] == "operator_pinned_working_set"
    assert payload["gravity_tax_accrual"] == "manual_review_queue"
    assert payload["retention_decision_state"] == "archive_after_checkpoint"
    assert payload["promotion_state"] == "promotion_deferred"
    assert payload["consolidation_readiness"] == "checkpoint_pending"
