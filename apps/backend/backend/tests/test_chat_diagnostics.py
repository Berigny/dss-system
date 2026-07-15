from __future__ import annotations

from backend.api.chat import (
    _apply_turn_diagnostics,
    _canonical_autonomy_decision,
    _canonical_candidate_trace,
    _delegated_prompt_path_metadata,
    _diagnostics_snapshot,
)


def test_canonical_candidate_trace_emits_payload_loaded_alias() -> None:
    trace = _canonical_candidate_trace(
        [
            {
                "coord": "chat-demo:WX-2",
                "relevance_score": 0.9,
                "tier_rank": 3,
                "resolved_payload_present": True,
                "explicit": True,
                "source": "explicit",
            }
        ]
    )
    assert len(trace) == 1
    assert trace[0]["coord"] == "chat-demo:WX-2"
    assert trace[0]["coord_type"] == "WX"
    assert trace[0]["origin_attestation"] == "explicit_user_referenced_coord"
    assert trace[0]["relevance_tier"] == 1
    assert trace[0]["payload_state"] == "opened"
    assert trace[0]["recommended_action"] == "reuse_already_opened"
    assert trace[0]["resolved_payload_present"] is True
    assert trace[0]["payload_loaded"] is True


def test_canonical_candidate_trace_preserves_ancestry_signals() -> None:
    trace = _canonical_candidate_trace(
        [
            {
                "coord": "chat-demo:WX-3",
                "relevance_score": 0.82,
                "tier_rank": 2,
                "resolved_payload_present": False,
                "p_adic_similarity": 0.61,
            }
        ]
    )
    assert len(trace) == 1
    assert trace[0]["coord"] == "chat-demo:WX-3"
    assert trace[0]["ancestry_linked"] is True
    assert trace[0]["ancestry_score"] == 0.61


def test_canonical_candidate_trace_preserves_continuity_source() -> None:
    trace = _canonical_candidate_trace(
        [
            {
                "coord": "chat-demo:WX-4",
                "relevance_score": 0.44,
                "tier_rank": 1,
                "continuity_source": "introspect_latest_turn",
                "source": "recent",
            }
        ]
    )
    assert len(trace) == 1
    assert trace[0]["coord_type"] == "WX"
    assert trace[0]["origin_attestation"] == "model_response_wx"
    assert trace[0]["payload_state"] == "sealed"
    assert trace[0]["recommended_action"] == "walk_referenced_coord"
    assert trace[0]["skip_reason"] == "assistant_output_demoted_to_continuity_lane"
    assert trace[0]["continuity_source"] == "introspect_latest_turn"


def test_apply_turn_diagnostics_overrides_untrusted_metadata_fields() -> None:
    metadata_payload = {
        "ledger_id": "chat-demo",
        "runtime_namespace": "chat-demo",
        "runtime_identity": {
            "principal_did": "did:key:z6Mkexample123",
            "principal_canonical_subject": "did:web:id.dualsubstrate.com:principals:david",
            "library_boundary": {
                "canonical_ledger_id": "chat-demo",
                "registry_source": "registered_ledger_v1",
                "river_reads_policy_bounded": True,
                "river_mutates_library_directly": False,
                "hot_path_mode": "summary_only",
                "latency_boundary": {
                    "hot_path_budgeted": True,
                    "deep_history_requires_fallback_or_deferral": True,
                },
                "foundation_identity": {
                    "name": "LOAM",
                    "purpose": "Carry governed support memory.",
                    "source": "control_plane_operator",
                },
                "alias_history": ["ledger:chat-demo"],
                "supersession_history": [],
                "consolidation_history_count": 0,
            },
            "vc_refs": {
                "credential_ref": "vc:cred-1",
                "standing_envelope_ref": "vc:standing-1",
                "wallet_binding_ref": "vc:binding-1",
            },
        },
        "assurance_verification": {"status": "valid"},
        "candidate_trace": [{"coord": "bad:WX-1", "relevance_score": 0.01, "tier_rank": 0}],
        "autonomy_decision": {"action": "answer_from_priors", "top_k": []},
        "eq9_eval": {
            "checks": {
                "score": {"current": 0.91, "status": "pass"},
                "law": {"current": 0.83, "status": "pass"},
                "drift": {"current": 0.08, "status": "pass"},
                "meaning_per_token": {"current": 0.44, "status": "pass"},
            },
            "output_tokens": 142,
            "known_checks": 5,
            "on_track": True,
        },
        "introspect_snapshot_pre": {"latest_turn_coordinate": "chat-demo:WX-prev"},
        "introspect_snapshot_post": {
            "latest_turn_coordinate": "chat-demo:WX-post",
            "hysteresis_coherence": 0.71,
            "walk": {"walk_hops": 3},
        },
        "eval_contract": {"pass": True},
        "posture_policy": {"policy_decision": "allow", "reason_code": "baseline_satisfied", "policy_gate_version": "v1"},
        "standing_policy": {"write_commit_allowed": True, "retrieval_allowed": True, "max_output_tokens": 512},
        "consistency_check": {"status": "ok"},
    }
    candidates = [
        {
            "coord": "chat-demo:WX-99",
            "relevance_score": 0.95,
            "tier_rank": 3,
            "resolved_payload_present": True,
            "source": "retrieved",
            "ancestry_score": 0.72,
            "ancestry_linked": True,
        }
    ]
    decision = _canonical_autonomy_decision(
        {"policy": "balanced", "action": "resolve", "chosen_coord": "chat-demo:WX-99"},
        candidate_trace=candidates,
    )

    _apply_turn_diagnostics(
        metadata_payload,
        autonomy_candidates=candidates,
        autonomy_decision=decision,
    )

    snapshot = _diagnostics_snapshot(metadata_payload)
    assert snapshot["candidate_trace"][0]["coord"] == "chat-demo:WX-99"
    assert snapshot["autonomy_decision"]["action"] == "resolve"
    assert snapshot["autonomy_decision"]["top_k"][0]["coord"] == "chat-demo:WX-99"
    ancestry = snapshot["ancestry_recall"]
    assert ancestry["contract_version"] == "ancestry-recall-v2"
    assert ancestry["claim_posture"] == "resolved_prior_access_present"
    assert ancestry["explicit_surface_status"] == "explicit"
    assert ancestry["generic_history_fields_rejected_as_evidence"] is True
    assert ancestry["canonical_ledger_resolution"]["canonical_ledger_id"] == "chat-demo"
    assert ancestry["canonical_ledger_resolution"]["alias_or_consolidation_present"] is True
    assert ancestry["library_hot_path_summary_read"]["enabled"] is True
    assert ancestry["library_hot_path_summary_read"]["mode"] == "summary_only"
    assert ancestry["prior_payload_or_coord_access"]["present"] is True
    assert ancestry["prior_payload_or_coord_access"]["basis"] == ["resolved_payload_present"]
    assert ancestry["prior_payload_or_coord_access"]["coord_resolved_access_is_not_foundation_identity_rehydration"] is True
    assert ancestry["foundation_identity_rehydration"]["available"] is True
    assert ancestry["foundation_identity_rehydration"]["fields"]["name"] == "LOAM"
    assert ancestry["ancestry_linked_records"][0]["coord"] == "chat-demo:WX-99"
    assert ancestry["selection_rationale"]["chosen_coord"] == "chat-demo:WX-99"
    observability = snapshot["diagnostic_observability"]
    assert observability["contract_version"] == "diagnostic-observability-v1"
    assert observability["observational_not_experiential"] is True
    assert observability["manual_validation_categories"] == ["explicit", "indirect", "absent"]
    assert observability["present_observables"]["EQ9"] == [
        "score",
        "law",
        "drift",
        "output_tokens",
        "meaning_per_token",
    ]
    assert observability["absent_observables"] == ["EQ6"]
    assert observability["indirect_only_evidence"]["contradiction_indicators"] == []
    assert observability["indirect_only_evidence"]["rule"] == "contradiction_only_evidence_does_not_count_as_explicit_contract_surface"
    assert observability["upstream_boundary"]["base4_runtime_posture_visible"] is True
    assert observability["upstream_boundary"]["library_summary_boundary_visible"] is True
    assert observability["upstream_boundary"]["canonical_ledger_id"] == "chat-demo"
    assert observability["upstream_boundary"]["hot_path_mode"] == "summary_only"
    assert observability["allowed_claim_mapping"]["EQ6"] == "claim_only_when_present_in_current_runtime_context"
    base4 = snapshot["base4_runtime_state"]
    assert base4["contract_version"] == "base4-runtime-state-v1"
    assert base4["state_model"] == ["Halt", "Probe", "Stabilise", "Express"]
    assert base4["state"] == "Express"
    assert base4["reason"] == "publishable_grounded_output"
    assert base4["runtime_posture_only"] is True
    assert base4["intervention_boundary"]["required"] is False
    assert base4["latency_aware_posture"]["hot_path_bounded"] is True
    assert base4["latency_aware_posture"]["fallback_or_deferral_expected_when_near_cap"] is False
    assert base4["evidence"]["policy_decision"] == "allow"
    assert base4["evidence"]["selected_action"] == "resolve"
    continuity = snapshot["self_model_continuity"]
    assert continuity["contract_version"] == "self-model-continuity-v1"
    assert continuity["non_phenomenological"] is True
    assert continuity["upstream_substrate_dependencies"]["base4_runtime_posture_visible"] is True
    assert continuity["upstream_substrate_dependencies"]["library_summary_boundary_visible"] is True
    assert continuity["upstream_substrate_dependencies"]["governed_retention_visible"] is False
    primitives = continuity["primitives"]
    assert primitives["SelfObservationRecord"]["present"] is True
    assert primitives["SelfObservationRecord"]["evidence"]["latest_turn_coordinate"] == "chat-demo:WX-prev"
    assert primitives["RuntimeGoal"]["present"] is True
    assert primitives["RuntimeGoal"]["evidence"]["evaluative_basis"]["goal_source"] == "eval_contract"
    assert primitives["RuntimeGoal"]["evidence"]["evaluative_basis"]["policy_decision"] == "allow"
    assert primitives["SalienceScore"]["present"] is True
    assert primitives["PredictionRecord"]["present"] is True
    assert primitives["ErrorSignal"]["present"] is True
    assert primitives["ValuationSignal"]["present"] is True
    assert primitives["ValuationSignal"]["evidence"]["evaluative_basis"]["eq9_on_track"] is True
    assert primitives["ValuationSignal"]["evidence"]["evaluative_basis"]["score_present"] is True
    assert primitives["ValuationSignal"]["evidence"]["evaluative_basis"]["law_present"] is True
    assert primitives["ValuationSignal"]["evidence"]["evaluative_basis"]["drift_present"] is True
    persistence = snapshot["between_turn_persistence"]
    assert persistence["contract_version"] == "between-turn-persistence-v1"
    assert persistence["runtime_capability_only"] is True
    assert persistence["explicit_surface_status"] == "explicit"
    assert persistence["generic_session_markers_rejected_as_evidence"] is True
    assert persistence["canonical_ledger_resolution"]["canonical_ledger_id"] == "chat-demo"
    assert persistence["canonical_ledger_resolution"]["alias_history_count"] == 1
    assert persistence["retention_tier_truth"]["active_continuity_tier"] == "Silt"
    assert persistence["retention_tier_truth"]["durable_tier_visible"] is None
    assert persistence["state_surfaces"]["has_pre_snapshot"] is True

    assert persistence["background_state_tick"]["named"] is True
    tension = snapshot["unresolved_tension_and_commit"]
    assert tension["contract_version"] == "unresolved-tension-v1"
    assert tension["operational_not_anthropomorphic"] is True
    assert tension["explicit_surface_status"] == "absent"
    assert tension["indirect_only_evidence"]["rule"] == "contradiction_only_evidence_is_indirect_until_named_unresolved_tension_objects_are_present"
    assert tension["indirect_only_evidence"]["counts_as_explicit_only_when_tracked_objects_exist"] is True
    assert tension["runtime_posture_boundary"]["base4_state"] == "Express"
    assert tension["runtime_posture_boundary"]["off_path_preferred"] is False
    assert tension["candidate_response_set"]["present"] is True
    assert tension["candidate_response_set"]["candidate_count"] == 1
    assert tension["resolution_decision"]["selected_action"] == "resolve"
    assert tension["resolution_decision"]["deferred_commit"]["applied"] is False
    assert tension["unresolved_tension"]["present"] is False
    retention = snapshot["bounded_retention_pressure"]
    assert retention["contract_version"] == "bounded-retention-v1"
    assert retention["operational_not_existential"] is True
    assert retention["explicit_surface_status"] == "explicit"
    assert retention["output_token_limits_alone_rejected_as_evidence"] is True
    assert retention["salience_valence_markers"][0]["marker"] == "clarifying"
    assert retention["salience_valence_markers"][1]["marker"] == "constraint_relevant"
    assert retention["persistence_budget"]["bounded_runtime_budget"] is True
    assert retention["retention_tier_truth"]["retention_tier"] == "Clay"
    assert retention["retention_tier_truth"]["retention_tier_reason"] == "durable_ledger_write_path"
    assert retention["gravity_tax_linkage"]["explicit_retention_cost_policy"] is True
    assert retention["gravity_tax_linkage"]["governed_promotion_required"] is True
    gravity_tax = snapshot["gravity_tax_retention_policy"]
    assert gravity_tax["contract_version"] == "gravity-tax-v1"
    assert gravity_tax["explicit_retention_cost_policy"] is True
    assert gravity_tax["anti_hoarding_posture"] == "selective_retention_over_silent_accumulation"
    assert gravity_tax["governed_promotion_required"] is True
    assert gravity_tax["noisy_or_low_coherence_drains_by_default"] is True
    assert gravity_tax["cost_inputs"]["eq4_coupling_live_input"] is True
    assert gravity_tax["cost_inputs"]["eq5_persistence_cost_live_input"] is True
    assert gravity_tax["evidence"]["retention_tier"] == "Clay"
    assert gravity_tax["evidence"]["retention_tier_reason"] == "durable_ledger_write_path"
    assert gravity_tax["evidence"]["gravity_cost"] is None
    assert gravity_tax["evidence"]["gravity_penalty"] is None
    assert retention["retention_decision"]["reviewable"] is True
    assert retention["retention_decision"]["decisions"][0]["decision"] == "retain"
    assert retention["retention_decision"]["decisions"][0]["coord"] == "chat-demo:WX-99"
    autonomy_memory = snapshot["autonomy_outcome_memory"]
    assert autonomy_memory["contract_version"] == "autonomy-pattern-v1"
    assert autonomy_memory["outcome_oriented_only"] is True
    pattern = autonomy_memory["autonomy_pattern"]
    assert pattern["kind"] == "autonomy_pattern"
    assert pattern["ledger_id"] == "chat-demo"
    assert pattern["phase"] == "between_turns"
    assert pattern["pattern"]["coord_type"] == "WX"
    assert pattern["pattern"]["recursion_depth_observed"] == 3
    assert pattern["pattern"]["grounding_success"] is True
    assert pattern["pattern"]["agent_chose_depth"] is True
    assert pattern["pattern"]["decision_basis"] == "open_query_with_resolved_support"
    assert pattern["pattern"]["governing_basis"] == "allow:baseline_satisfied"
    assert pattern["pattern"]["purpose_anchor"] == "grounded_answer_with_traceable_support"
    assert pattern["pattern"]["refs_used"] == ["chat-demo:WX-99"]
    assert pattern["ttl"] == "session"
    assert autonomy_memory["canonical_ledger_resolution"]["canonical_ledger_id"] == "chat-demo"
    assert autonomy_memory["canonical_ledger_resolution"]["alias_history_count"] == 1
    assert autonomy_memory["retention_tier_truth"]["retention_tier"] == "Clay"
    learned_profile = snapshot["learned_autonomy_profile"]
    assert learned_profile["contract_version"] == "learned-autonomy-profile-v1"
    assert learned_profile["derived_not_persistent_selfhood"] is True
    assert learned_profile["traceable_to_autonomy_patterns"] is True
    assert learned_profile["canonical_ledger_resolution"]["canonical_ledger_id"] == "chat-demo"
    assert learned_profile["canonical_ledger_resolution"]["alias_history_count"] == 1
    assert learned_profile["hot_path_consumption_boundary"]["summary_first"] is True
    assert learned_profile["hot_path_consumption_boundary"]["hot_path_mode"] == "summary_only"
    profile = learned_profile["profile"]
    assert profile["preferred_recursion_depth_by_prompt_class"][0]["prompt_class"] == "open_query"
    assert profile["preferred_recursion_depth_by_prompt_class"][0]["preferred_depth"] == 3
    assert profile["preferred_recursion_depth_by_prompt_class"][0]["governing_basis"] == "allow:baseline_satisfied"
    assert profile["preferred_recursion_depth_by_prompt_class"][0]["purpose_anchor"] == "grounded_answer_with_traceable_support"
    assert profile["productive_coord_families"][0]["coord_type"] == "WX"
    assert profile["productive_coord_families"][0]["decision_basis"] == "open_query_with_resolved_support"
    assert profile["productive_coord_families"][0]["governing_basis"] == "allow:baseline_satisfied"
    assert profile["action_preferences"][0]["action"] == "resolve"
    assert profile["action_preferences"][0]["decision_basis"] == "open_query_with_resolved_support"
    assert profile["action_preferences"][0]["purpose_anchor"] == "grounded_answer_with_traceable_support"
    assert profile["deeper_walk_hint"] == "prefer deeper resolved walk for open queries"
    traceability = learned_profile["traceability"]
    assert traceability["source_pattern_count"] == 1
    assert traceability["source_patterns"][0]["kind"] == "autonomy_pattern"
    profile_snapshot = snapshot["readable_profile_snapshot"]
    assert profile_snapshot["contract_version"] == "readable-profile-snapshot-v2"
    assert profile_snapshot["summary_first"] is True
    assert profile_snapshot["not_full_latent_transparency"] is True
    assert profile_snapshot["identity_assurance_posture"]["level"] == "strong"
    assert profile_snapshot["canonical_ledger_resolution"]["canonical_ledger_id"] == "chat-demo"
    principal_boundary = profile_snapshot["principal_readable_boundary"]
    assert principal_boundary["ledger_id"] == "chat-demo"
    assert principal_boundary["principal_did"] == "did:key:z6Mkexample123"
    assert principal_boundary["vc_ref_counts"]["present"] == 3
    assert principal_boundary["identity_layers"]["founding_constitution_distinct_from_verified_traits"] is True
    assert principal_boundary["identity_layers"]["resolved_constitution_context_distinct_from_runtime_foundation_identity"] is True
    assert principal_boundary["resolved_constitution_context"]["present"] is True
    assert principal_boundary["runtime_foundation_identity"]["available"] is True
    assert principal_boundary["profile_claims"][0]["field"] == "preferred_recursion_depth"
    assert principal_boundary["profile_claims"][0]["summary"]["governing_basis"] == "allow:baseline_satisfied"
    assert principal_boundary["profile_claims"][0]["summary"]["purpose_anchor"] == "grounded_answer_with_traceable_support"
    assert principal_boundary["profile_claims"][1]["summary"]["decision_basis"] == "open_query_with_resolved_support"
    assert principal_boundary["profile_claims"][1]["summary"]["governing_basis"] == "allow:baseline_satisfied"
    assert principal_boundary["profile_claims"][2]["summary"]["decision_basis"] == "open_query_with_resolved_support"
    assert principal_boundary["profile_claims"][2]["summary"]["purpose_anchor"] == "grounded_answer_with_traceable_support"
    self_profile = snapshot["aggregate_agent_self_profile"]
    assert self_profile["contract_version"] == "aggregate-agent-self-profile-v1"
    assert self_profile["aggregate_only"] is True
    assert self_profile["no_per_principal_raw_leakage"] is True
    assert self_profile["source"]["canonical_ledger_id"] == "chat-demo"
    assert self_profile["source"]["resolved_constitution_context_present"] is True
    assert self_profile["source"]["runtime_foundation_identity_available"] is True
    assert self_profile["learned_patterns"][0]["field"] == "preferred_recursion_depth"
    assert self_profile["learned_patterns"][0]["governing_basis"] == "allow:baseline_satisfied"
    assert self_profile["learned_patterns"][0]["purpose_anchor"] == "grounded_answer_with_traceable_support"
    boundary = snapshot["river_library_boundary"]
    assert boundary["contract_version"] == "river-library-boundary-v1"
    assert "live_inference" in boundary["roles"]["River"]
    assert "founding_constitution" in boundary["roles"]["Library"]
    assert boundary["read_boundary"]["policy_bounded"] is True
    assert boundary["read_boundary"]["library_reads_allowed"] is True
    assert boundary["read_boundary"]["hot_path_mode"] == "summary_only"
    assert boundary["mutation_boundary"]["river_may_read_library"] is True
    assert boundary["mutation_boundary"]["river_may_mutate_library_directly"] is False
    assert boundary["continuity_rehydration"]["canonical_ledger_id"] == "chat-demo"
    assert boundary["continuity_rehydration"]["foundation_identity_available"] is True
    assert boundary["continuity_rehydration"]["alias_history_count"] == 1
    assert boundary["latency_boundary"]["hot_path_budgeted"] is True
    assert boundary["latency_boundary"]["deep_history_requires_fallback_or_deferral"] is True
    consent = snapshot["consent_registry"]
    assert consent["contract_version"] == "consent-registry-v1"
    assert consent["declarative_scope_registry"] is True
    assert consent["administratively_bounded"] is True
    assert consent["canonical_ledger_resolution"]["canonical_ledger_id"] == "chat-demo"
    assert consent["canonical_ledger_resolution"]["alias_history_count"] == 1
    assert consent["authority_basis"]["compact_and_operational"] is True
    assert consent["authority_basis"]["authenticated_principal_present"] is True
    assert consent["authority_basis"]["verification_present"] is True
    assert consent["authority_basis"]["declared_end"] == "profile_level_learning_scope_governance"
    assert consent["authority_basis"]["distinctions"]["authenticated"] is True
    assert consent["authority_basis"]["distinctions"]["permitted"] is True
    assert consent["authority_basis"]["distinctions"]["authorized"] is True
    assert consent["authority_basis"]["rule"] == "authentication_or_verification_alone_do_not_confer_scope_authority"
    assert consent["identity_assurance_posture"]["level"] == "strong"
    assert consent["identity_assurance_posture"]["consent_strength"] == "strong"
    assert consent["scopes"][0]["scope"] == "learning.style"
    assert consent["scopes"][0]["allowed"] is True
    assert consent["scopes"][0]["declared_end"] == "improve_reasoning_style_continuity"
    assert consent["scopes"][1]["scope"] == "learning.opinions"
    assert consent["scopes"][1]["allowed"] is False
    assert consent["scopes"][1]["declared_end"] == "blocked_without_explicit_authorization"
    assert consent["commit_time_scope_enforcement"]["violation_result"] == "commit_rejected"
    assert consent["weaker_posture_fallback"]["consent_acts_provisional"] is False
    delta = snapshot["profile_delta_record"]
    assert delta["contract_version"] == "profile-delta-record-v1"
    assert delta["compact_attribution_only"] is True
    assert delta["canonical_ledger_resolution"]["canonical_ledger_id"] == "chat-demo"
    assert delta["delta"]["scope"] == "learning.style"
    assert delta["delta"]["decision_basis"] == "open_query_with_resolved_support"
    assert delta["delta"]["scope_authority"]["governing_basis"] == "allow:baseline_satisfied"
    assert delta["delta"]["scope_authority"]["authorized"] is True
    assert delta["delta"]["scope_authority"]["declared_end"] == "profile_level_learning_scope_governance"
    assert delta["delta"]["purpose_anchor"] == "grounded_answer_with_traceable_support"
    assert delta["delta"]["profile_after"]["preferred_recursion_depth"] == 3
    assert delta["delta"]["profile_after"]["productive_coord_family"] == "WX"
    assert delta["delta"]["scope_check_result"] == "pass"
    assert delta["persistence_posture"]["async_preferred"] is True
    revocation = snapshot["revocation_permit"]
    assert revocation["contract_version"] == "revocation-permit-v1"
    assert revocation["forward_scope_blocking"] is True
    assert revocation["bounded_retroactive_rollback"] is True
    assert revocation["canonical_ledger_resolution"]["canonical_ledger_id"] == "chat-demo"
    assert revocation["authority_requirement"]["identity_assurance_posture"] == "strong"
    assert revocation["authority_requirement"]["scope_authority_basis"] == "profile_level_learning_scope_governance"
    assert revocation["permit_shape"]["effective_mode"] == "forward_or_retroactive"
    assert revocation["permit_shape"]["retroactive_window_days"] == 7
    influence_audit = snapshot["cross_principal_influence_audit"]
    assert influence_audit["contract_version"] == "cross-principal-influence-audit-v1"
    assert influence_audit["anonymized_reporting"] is True
    assert influence_audit["bounded_approximate_influence"] is True
    assert influence_audit["no_direct_principal_disclosure"] is True
    assert influence_audit["canonical_ledger_resolution"]["canonical_ledger_id"] == "chat-demo"
    assert influence_audit["canonical_ledger_resolution"]["alias_history_count"] == 1
    assert influence_audit["read_boundary"]["summary_first"] is True
    assert influence_audit["read_boundary"]["hot_path_mode"] == "summary_only"
    assert influence_audit["basis_distinction"]["raw_influence_is_not_evaluative_basis"] is True
    assert influence_audit["basis_distinction"]["raw_influence_is_not_governing_basis"] is True
    assert (
        influence_audit["basis_distinction"]["rule"]
        == "influence_traces_do_not_by_themselves_explain_what_the_system_ought_to_optimize_for"
    )
    assert influence_audit["audit_posture"]["identity_assurance_posture"] == "strong"
    assert influence_audit["audit_posture"]["access_strength"] == "strong"
    assert influence_audit["query_surfaces"] == ["response_level", "profile_level"]
    assert influence_audit["response_level"]["influence_records"][0]["principal_hash"] == "sha256:anonymous"
    assert influence_audit["response_level"]["influence_records"][0]["evaluative_basis_claimed"] is False
    assert influence_audit["response_level"]["influence_records"][0]["governing_basis_claimed"] is False
    assert influence_audit["profile_level"]["direct_principal_disclosure"] is False
    assert influence_audit["profile_level"]["evaluative_basis_claimed"] is False
    assert influence_audit["profile_level"]["governing_basis_claimed"] is False
    enrichment = snapshot["between_turn_enrichment"]
    assert enrichment["contract_version"] == "between-turn-enrichment-v2"
    assert enrichment["system_scheduled_not_spontaneous"] is True
    assert enrichment["continuity_infrastructure_not_background_agent"] is True
    assert enrichment["explicit_surface_status"] == "explicit"
    assert enrichment["generic_session_history_rejected_as_evidence"] is True
    assert enrichment["promotion_boundary"]["source_tiers"] == ["Sand", "Silt"]
    assert enrichment["promotion_boundary"]["target_tiers"] == ["Silt", "Clay"]
    assert enrichment["canonical_ledger_resolution"]["canonical_ledger_id"] == "chat-demo"
    assert enrichment["canonical_ledger_resolution"]["alias_history_count"] == 1
    assert enrichment["latency_boundary"]["hot_path_mode"] == "summary_only"
    assert enrichment["prior_payload_context"]["attributable_resolved_context_present"] is True
    assert enrichment["prior_payload_context"]["resolved_context_is_not_foundation_identity_rehydration"] is True
    assert enrichment["foundation_identity_rehydration"]["available"] is True
    assert enrichment["between_turn_enrichment"]["posture"] == "system_scheduled_and_policy_bounded"
    assert enrichment["between_turn_enrichment"]["may_do"] == [
        "summarize_autonomy_outcomes",
        "emit_autonomy_patterns",
        "update_learned_autonomy_profile",
        "preserve_compact_purpose_anchor_context",
        "carry_forward_attributable_resolved_prior_context",
    ]
    assert enrichment["between_turn_enrichment"]["observable_inputs"]["purpose_anchor"] == "grounded_answer_with_traceable_support"
    assert enrichment["enrichment_budget"]["token_budget"]["bounded"] is True
    assert enrichment["enrichment_budget"]["write_permission"]["allowed"] is True
    consolidation = snapshot["bounded_async_consolidation_bridge"]
    assert consolidation["contract_version"] == "bounded-consolidation-bridge-v1"
    assert consolidation["phase_boundary"] == "phase_2_bridge_only"
    assert consolidation["bounded_async_only"] is True
    assert consolidation["off_hot_path_by_default"] is True
    assert consolidation["latency_relief_explicit"] is True
    assert consolidation["consent_and_revocation_checkpoints_required"] is True
    assert consolidation["speculative_sleep_or_retrocausal_claims_rejected"] is True
    assert consolidation["bridge_scope"]["may_do"] == [
        "bounded_async_replay",
        "bounded_async_pruning",
        "selective_sand_to_silt_or_clay_promotion",
    ]
    assert consolidation["promotion_boundary"]["source_tiers"] == ["Sand", "Silt"]
    assert consolidation["promotion_boundary"]["target_tiers"] == ["Silt", "Clay"]
    assert consolidation["promotion_boundary"]["target_tier_if_triggered"] == "Clay"
    assert consolidation["checkpoints"]["consent_registry_required"] is True
    assert consolidation["checkpoints"]["revocation_permit_required"] is True
    assert consolidation["latency_boundary"]["interactive_path"] == "summary_only_or_skip"
    assert consolidation["latency_boundary"]["deeper_replay_requires"] == "fallback_or_deferral"
    assert consolidation["evidence"]["retention_tier"] == "Clay"
    assert consolidation["evidence"]["purpose_anchor"] == "grounded_answer_with_traceable_support"
    assert consolidation["evidence"]["write_commit_allowed"] is True


def test_diagnostics_snapshot_base4_runtime_state_uses_latency_probe_boundary() -> None:
    snapshot = _diagnostics_snapshot(
        {
            "candidate_trace": [
                {
                    "coord": "chat-demo:WX-latency",
                    "relevance_score": 0.61,
                    "tier_rank": 2,
                    "resolved_payload_present": True,
                    "source": "retrieved",
                }
            ],
            "autonomy_decision": {"policy": "balanced", "action": "answer_from_priors"},
            "posture_policy": {
                "policy_decision": "allow",
                "reason_code": "baseline_satisfied",
                "policy_gate_version": "v1",
            },
            "standing_policy": {
                "write_commit_allowed": True,
                "max_output_tokens": 128,
            },
            "context_window": {
                "completion_tokens": 128,
                "prompt_tokens": 900,
                "retrieved_count": 1,
                "history_len": 4,
            },
            "max_tokens": 128,
            "finish_reason": "length",
            "eq9_eval": {
                "checks": {
                    "score": {"current": 0.82, "status": "pass"},
                    "law": {"current": 0.74, "status": "pass"},
                    "drift": {"current": 0.11, "status": "pass"},
                },
                "known_checks": 3,
                "on_track": True,
            },
        }
    )

    base4 = snapshot["base4_runtime_state"]
    assert base4["state"] == "Probe"
    assert base4["reason"] == "latency_budget_pressure"
    assert base4["intervention_boundary"]["required"] is False
    assert base4["latency_aware_posture"]["budget_pressure"] == "near_cap"
    assert base4["latency_aware_posture"]["fallback_or_deferral_expected_when_near_cap"] is True


def test_delegated_prompt_path_metadata_separates_requested_and_prompting_principals() -> None:
    delegated = _delegated_prompt_path_metadata(
        None,
        {
            "delegated_prompt_path_active": True,
            "delegated_cli_request": True,
            "delegated_by_principal_did": "did:key:z6MkOperator",
            "delegated_by_principal_id": "operator:david",
            "delegation_mode": "delegated_only",
            "delegated_surface_id": "surface:chat:primary",
            "delegated_ledger_scope": ["chat-demo"],
            "delegated_surface_scope": ["surface:chat:primary"],
            "delegation_expires_at": "2026-05-05T10:00:00+00:00",
        },
        {
            "contributor": {
                "principal_type": "agent",
                "principal_id": "openai:codex",
                "principal_did": "did:web:id.dualsubstrate.com:principals:agent:openai:codex",
            },
            "runtime_identity": {
                "ledger_id": "chat-demo",
            },
        },
    )
    assert delegated is not None
    assert delegated["audit_posture"] == "requested_by_operator_executed_by_delegated_principal"
    assert delegated["prompt_principal_id"] == "openai:codex"
    assert delegated["prompt_principal_did"] == "did:web:id.dualsubstrate.com:principals:agent:openai:codex"
    assert delegated["requested_by_principal_did"] == "did:key:z6MkOperator"
    assert delegated["requested_by_principal_id"] == "operator:david"
    assert delegated["requested_by_is_distinct_from_prompt_principal"] is True
    assert delegated["target_ledger_id"] == "chat-demo"
    assert delegated["target_surface_id"] == "surface:chat:primary"
    assert delegated["cli_request_required"] is True


def test_diagnostic_observability_reports_present_eq6_when_available() -> None:
    snapshot = _diagnostics_snapshot(
        {
            "e6_diagnostics": {
                "mode": "two_pass",
                "route": "bridge",
                "quality_tier": "Q2",
                "bridge_allowed_runtime": True,
            }
        }
    )
    observability = snapshot["diagnostic_observability"]
    assert observability["absent_observables"] == []
    assert observability["present_observables"]["EQ6"] == [
        "mode",
        "route",
        "quality_tier",
        "bridge_allowed_runtime",
        "promotion_allowed",
        "promotion_reason",
    ]
    assert observability["upstream_boundary"]["base4_runtime_posture_visible"] is False
    assert observability["upstream_boundary"]["library_summary_boundary_visible"] is False
    assert observability["upstream_boundary"]["hot_path_mode"] == "summary_only"


def test_diagnostic_observability_tracks_indirect_contradiction_evidence() -> None:
    snapshot = _diagnostics_snapshot(
        {
            "posture_policy": {"policy_decision": "block"},
            "standing_policy": {"write_commit_allowed": True},
            "eq9_eval": {"on_track": False, "checks": {}},
        }
    )

    observability = snapshot["diagnostic_observability"]
    assert observability["present_observables"]["EQ9"] == []
    assert observability["indirect_only_evidence"]["contradiction_indicators"] == [
        "policy_block_with_write_commit_allowed",
        "eq9_not_on_track",
    ]
    assert observability["upstream_boundary"]["base4_runtime_posture_visible"] is True
    assert observability["upstream_boundary"]["library_summary_boundary_visible"] is False


def test_ancestry_recall_marks_absent_without_explicit_ancestry_records() -> None:
    metadata_payload = {
        "candidate_trace": [
            {
                "coord": "chat-demo:WX-11",
                "relevance_score": 0.41,
                "tier_rank": 1,
                "resolved_payload_present": True,
                "source": "recent",
            }
        ],
        "autonomy_decision": {"policy": "balanced", "action": "resolve", "chosen_coord": "chat-demo:WX-11"},
    }

    snapshot = _diagnostics_snapshot(metadata_payload)
    ancestry = snapshot["ancestry_recall"]

    assert ancestry["claim_posture"] == "candidate_ranked_only"
    assert ancestry["explicit_surface_status"] == "absent"
    assert ancestry["generic_history_fields_rejected_as_evidence"] is True
    assert ancestry["canonical_ledger_resolution"]["canonical_ledger_id"] is None
    assert ancestry["canonical_ledger_resolution"]["alias_or_consolidation_present"] is False
    assert ancestry["library_hot_path_summary_read"]["enabled"] is False
    assert ancestry["library_hot_path_summary_read"]["mode"] == "summary_only"
    assert ancestry["prior_payload_or_coord_access"]["present"] is False
    assert ancestry["foundation_identity_rehydration"]["available"] is False
    assert ancestry["ancestry_linked_records"] == []


def test_ancestry_recall_distinguishes_direct_decode_from_foundation_identity_rehydration() -> None:
    metadata_payload = {
        "runtime_identity": {
            "library_boundary": {
                "canonical_ledger_id": "chat-demo",
                "hot_path_mode": "summary_only",
                "foundation_identity": {
                    "name": None,
                    "purpose": None,
                    "source": None,
                },
            }
        },
        "resolve_summary": {
            "requested_count": 1,
            "resolved_count": 1,
            "resolved": ["chat-demo:WX-42"],
            "unresolved_count": 0,
        },
        "epistemic_status": {
            "method": "direct_decode",
            "opened_payload_coords": ["chat-demo:WX-42"],
            "source_coords": ["chat-demo:WX-42"],
        },
    }

    snapshot = _diagnostics_snapshot(metadata_payload)
    ancestry = snapshot["ancestry_recall"]

    assert ancestry["claim_posture"] == "resolved_prior_access_present"
    assert ancestry["prior_payload_or_coord_access"]["present"] is True
    assert ancestry["prior_payload_or_coord_access"]["basis"] == [
        "direct_decode",
        "opened_payload_coords",
        "source_coords",
        "resolved_coords",
    ]
    assert ancestry["prior_payload_or_coord_access"]["opened_payload_coords"] == ["chat-demo:WX-42"]
    assert ancestry["usage_trace"]["resolved_preview"] == ["chat-demo:WX-42"]
    assert ancestry["foundation_identity_rehydration"]["available"] is False
    assert ancestry["foundation_identity_rehydration"]["fields"] == {
        "name": None,
        "purpose": None,
        "source": None,
    }


def test_self_model_continuity_reports_absent_primitives_when_runtime_signals_missing() -> None:
    snapshot = _diagnostics_snapshot({})
    continuity_block = snapshot["self_model_continuity"]
    assert continuity_block["upstream_substrate_dependencies"]["base4_runtime_posture_visible"] is False
    assert continuity_block["upstream_substrate_dependencies"]["library_summary_boundary_visible"] is False
    assert continuity_block["upstream_substrate_dependencies"]["governed_retention_visible"] is False
    continuity = continuity_block["primitives"]
    assert continuity["SelfObservationRecord"]["present"] is False
    assert continuity["RuntimeGoal"]["present"] is False
    assert continuity["SalienceScore"]["present"] is False
    assert continuity["PredictionRecord"]["present"] is False
    assert continuity["ErrorSignal"]["present"] is False
    assert continuity["ValuationSignal"]["present"] is False
    assert continuity["RuntimeGoal"]["evidence"]["evaluative_basis"]["goal_source"] is None
    assert continuity["ValuationSignal"]["evidence"]["evaluative_basis"]["eq9_on_track"] is None
    persistence = snapshot["between_turn_persistence"]
    assert persistence["explicit_surface_status"] == "absent"
    assert persistence["generic_session_markers_rejected_as_evidence"] is True
    assert persistence["canonical_ledger_resolution"]["canonical_ledger_id"] is None
    assert persistence["retention_tier_truth"]["active_continuity_tier"] is None
    assert persistence["retention_tier_truth"]["durable_tier_visible"] is None
    assert persistence["state_surfaces"]["has_pre_snapshot"] is False
    assert persistence["state_surfaces"]["has_post_snapshot"] is False
    assert persistence["observable_state_changes"]["candidate_count"] == 0
    tension = snapshot["unresolved_tension_and_commit"]
    assert tension["runtime_posture_boundary"]["base4_state"] == "Express"
    assert tension["runtime_posture_boundary"]["off_path_preferred"] is False
    assert tension["candidate_response_set"]["present"] is False
    assert tension["resolution_decision"]["deferred_commit"]["mode"] == "immediate_commit"
    assert tension["unresolved_tension"]["present"] is False
    retention = snapshot["bounded_retention_pressure"]
    assert retention["explicit_surface_status"] == "absent"
    assert retention["output_token_limits_alone_rejected_as_evidence"] is True
    assert retention["salience_valence_markers"] == []
    assert retention["retention_decision"]["decisions"] == []
    assert retention["persistence_budget"]["pressure_state"] == "bounded"
    assert retention["retention_tier_truth"]["retention_tier"] == "Clay"
    assert retention["gravity_tax_linkage"]["explicit_retention_cost_policy"] is True
    autonomy_memory = snapshot["autonomy_outcome_memory"]
    assert autonomy_memory["autonomy_pattern"]["ledger_id"] is None
    assert autonomy_memory["autonomy_pattern"]["pattern"]["coord_type"] is None
    assert autonomy_memory["autonomy_pattern"]["pattern"]["recursion_depth_observed"] == 1
    assert autonomy_memory["autonomy_pattern"]["pattern"]["grounding_success"] is True
    assert autonomy_memory["canonical_ledger_resolution"]["canonical_ledger_id"] is None
    assert autonomy_memory["retention_tier_truth"]["retention_tier"] == "Clay"
    learned_profile = snapshot["learned_autonomy_profile"]
    assert learned_profile["canonical_ledger_resolution"]["canonical_ledger_id"] is None
    assert learned_profile["hot_path_consumption_boundary"]["summary_first"] is True
    assert learned_profile["profile"]["preferred_recursion_depth_by_prompt_class"][0]["prompt_class"] == "lightweight_query"
    assert learned_profile["profile"]["preferred_recursion_depth_by_prompt_class"][0]["governing_basis"] == "runtime_policy_not_explicit"
    assert learned_profile["profile"]["preferred_recursion_depth_by_prompt_class"][0]["purpose_anchor"] == "grounded_answer_with_traceable_support"
    assert learned_profile["profile"]["productive_coord_families"] == []
    assert learned_profile["profile"]["action_preferences"][0]["action"] == "answer_from_priors"
    assert learned_profile["profile"]["action_preferences"][0]["decision_basis"] == "concise_response_under_weaker_grounding"
    assert learned_profile["profile"]["action_preferences"][0]["purpose_anchor"] == "grounded_answer_with_traceable_support"
    profile_snapshot = snapshot["readable_profile_snapshot"]
    assert profile_snapshot["identity_assurance_posture"]["level"] == "weak"
    assert profile_snapshot["canonical_ledger_resolution"]["canonical_ledger_id"] is None
    assert profile_snapshot["clarifying_confidence_challenge"]["required_for_strong_claims"] is True
    assert profile_snapshot["principal_readable_boundary"]["resolved_constitution_context"]["present"] is False
    assert profile_snapshot["principal_readable_boundary"]["runtime_foundation_identity"]["available"] is False
    assert (
        profile_snapshot["principal_readable_boundary"]["profile_claims"][0]["summary"]["governing_basis"]
        == "runtime_policy_not_explicit"
    )
    assert (
        profile_snapshot["principal_readable_boundary"]["profile_claims"][0]["summary"]["purpose_anchor"]
        == "grounded_answer_with_traceable_support"
    )
    assert snapshot["aggregate_agent_self_profile"]["learned_patterns"][0]["field"] == "preferred_recursion_depth"
    assert snapshot["aggregate_agent_self_profile"]["source"]["canonical_ledger_id"] is None
    assert snapshot["aggregate_agent_self_profile"]["source"]["resolved_constitution_context_present"] is False
    assert snapshot["aggregate_agent_self_profile"]["source"]["runtime_foundation_identity_available"] is False
    assert snapshot["aggregate_agent_self_profile"]["learned_patterns"][0]["governing_basis"] == "runtime_policy_not_explicit"
    consent = snapshot["consent_registry"]
    assert consent["canonical_ledger_resolution"]["canonical_ledger_id"] is None
    assert consent["authority_basis"]["authenticated_principal_present"] is False
    assert consent["authority_basis"]["distinctions"]["authenticated"] is False
    assert consent["authority_basis"]["distinctions"]["permitted"] is True
    assert consent["authority_basis"]["distinctions"]["authorized"] is False
    assert consent["identity_assurance_posture"]["level"] == "weak"
    assert consent["identity_assurance_posture"]["consent_strength"] == "provisional"
    assert consent["weaker_posture_fallback"]["consent_acts_provisional"] is True
    assert consent["weaker_posture_fallback"]["bounded_clarification_allowed"] is True
    delta = snapshot["profile_delta_record"]
    assert delta["canonical_ledger_resolution"]["canonical_ledger_id"] is None
    assert delta["delta"]["decision_basis"] == "concise_response_under_weaker_grounding"
    assert delta["delta"]["scope_authority"]["authorized"] is False
    assert delta["delta"]["purpose_anchor"] == "grounded_answer_with_traceable_support"
    assert delta["delta"]["scope_check_result"] == "provisional"
    revocation = snapshot["revocation_permit"]
    assert revocation["canonical_ledger_resolution"]["canonical_ledger_id"] is None
    assert revocation["authority_requirement"]["scope_authority_basis"] == "profile_level_learning_scope_governance"
    assert revocation["authority_requirement"]["provisional_when_weak"] is True
    assert revocation["permit_shape"]["effective_mode"] == "forward_block"
    assert revocation["permit_shape"]["retroactive_window_days"] == 0
    influence_audit = snapshot["cross_principal_influence_audit"]
    assert influence_audit["canonical_ledger_resolution"]["canonical_ledger_id"] is None
    assert influence_audit["audit_posture"]["identity_assurance_posture"] == "weak"
    assert influence_audit["audit_posture"]["access_strength"] == "bounded"
    assert influence_audit["response_level"]["influence_records"][0]["evaluative_basis_claimed"] is False
    enrichment = snapshot["between_turn_enrichment"]
    assert enrichment["explicit_surface_status"] == "explicit"
    assert enrichment["generic_session_history_rejected_as_evidence"] is True
    assert enrichment["canonical_ledger_resolution"]["canonical_ledger_id"] is None
    assert enrichment["prior_payload_context"]["attributable_resolved_context_present"] is False
    assert enrichment["foundation_identity_rehydration"]["available"] is False
    assert enrichment["between_turn_enrichment"]["observable_inputs"]["ledger_id"] is None
    assert enrichment["between_turn_enrichment"]["observable_inputs"]["purpose_anchor"] == "grounded_answer_with_traceable_support"
    assert enrichment["enrichment_budget"]["write_permission"]["allowed"] is True
    consolidation = snapshot["bounded_async_consolidation_bridge"]
    assert consolidation["promotion_boundary"]["target_tier_if_triggered"] == "Clay"
    assert consolidation["evidence"]["write_commit_allowed"] is True


def test_unresolved_tension_contract_tracks_retry_and_unresolved_coords() -> None:
    snapshot = _diagnostics_snapshot(
        {
            "candidate_trace": [
                {
                    "coord": "chat-demo:WX-7",
                    "relevance_score": 0.77,
                    "tier_rank": 2,
                    "resolved_payload_present": True,
                    "source": "retrieved",
                }
            ],
            "autonomy_decision": {
                "policy": "balanced",
                "action": "resolve",
                "chosen_coord": "chat-demo:WX-7",
            },
            "consistency_check": {
                "status": "contradiction",
                "reason": "claims_unresolvable_with_resolved_context",
                "contradiction": True,
                "resolved_count": 1,
                "retried": True,
                "retry_count": 1,
                "retry_status": "applied",
            },
            "coord_resolution_warning": {
                "unresolved": ["chat-demo:WX-missing"],
                "blocked": False,
            },
            "ledger_id": "chat-demo",
            "context_window": {
                "prompt_tokens": 900,
                "completion_tokens": 128,
                "retrieved_count": 4,
                "history_len": 12,
            },
            "max_tokens": 128,
            "finish_reason": "length",
            "standing_policy": {
                "max_output_tokens": 128,
                "write_commit_allowed": False,
                "retrieval_allowed": True,
                "tool_scope": "chat",
                "retrieval_scope": "ledger",
            },
            "eq9_eval": {
                "known_checks": 5,
                "on_track": False,
            },
            "posture_policy": {
                "policy_decision": "allow",
                "reason_code": "baseline_satisfied",
            },
            "introspect_snapshot_pre": {
                "hysteresis_coherence": 0.42,
            },
            "introspect_snapshot_post": {
                "hysteresis_coherence": 0.57,
            },
        }
    )
    tension = snapshot["unresolved_tension_and_commit"]
    assert tension["explicit_surface_status"] == "explicit"
    assert tension["indirect_only_evidence"]["counts_as_explicit_only_when_tracked_objects_exist"] is True
    assert tension["runtime_posture_boundary"]["base4_state"] == "Probe"
    assert tension["runtime_posture_boundary"]["off_path_preferred"] is True
    assert tension["candidate_response_set"]["top_candidates"][0]["coord"] == "chat-demo:WX-7"
    assert tension["resolution_decision"]["deferred_commit"]["applied"] is True
    assert tension["resolution_decision"]["deferred_commit"]["mode"] == "single_retry_on_resolution_contradiction"
    assert tension["resolution_decision"]["deferred_commit"]["retry_count"] == 1
    assert tension["unresolved_tension"]["present"] is True
    tracked = tension["unresolved_tension"]["tracked_objects"]
    assert tracked[0]["kind"] == "resolution_consistency"
    assert tracked[1]["kind"] == "coord_resolution_gap"
    retention = snapshot["bounded_retention_pressure"]
    assert retention["explicit_surface_status"] == "explicit"
    assert retention["output_token_limits_alone_rejected_as_evidence"] is True
    assert retention["persistence_budget"]["pressure_state"] == "near_cap"
    assert retention["retention_tier_truth"]["retention_tier"] == "Clay"
    markers = retention["salience_valence_markers"]
    assert [marker["marker"] for marker in markers] == [
        "clarifying",
        "destabilizing",
        "constraint_relevant",
        "uncertainty_increasing",
    ]
    decisions = retention["retention_decision"]["decisions"]
    assert decisions[0]["reason"] == "top_ranked_salience"
    autonomy_memory = snapshot["autonomy_outcome_memory"]
    pattern = autonomy_memory["autonomy_pattern"]["pattern"]
    assert autonomy_memory["autonomy_pattern"]["ledger_id"] == "chat-demo"
    assert pattern["coord_type"] == "WX"
    assert pattern["recursion_depth_observed"] == 3
    assert pattern["coherence_delta"] == 0.15
    assert pattern["grounding_success"] is False
    assert pattern["decision_basis"] == "concise_response_under_weaker_grounding"
    assert pattern["governing_basis"] == "allow:baseline_satisfied"
    assert pattern["purpose_anchor"] == "bounded_response_under_constraint"
    assert pattern["next_instance_hint"] == "keep response concise unless stronger grounding signals appear"
    learned_profile = snapshot["learned_autonomy_profile"]
    profile = learned_profile["profile"]
    assert profile["preferred_recursion_depth_by_prompt_class"][0]["prompt_class"] == "open_query"
    assert profile["preferred_recursion_depth_by_prompt_class"][0]["governing_basis"] == "allow:baseline_satisfied"
    assert profile["preferred_recursion_depth_by_prompt_class"][0]["purpose_anchor"] == "bounded_response_under_constraint"
    assert profile["productive_coord_families"][0]["grounding_success_rate"] == 0.0
    assert profile["productive_coord_families"][0]["decision_basis"] == "concise_response_under_weaker_grounding"
    assert profile["productive_coord_families"][0]["governing_basis"] == "allow:baseline_satisfied"
    assert profile["action_preferences"][0]["grounding_success_rate"] == 0.0
    assert profile["action_preferences"][0]["decision_basis"] == "concise_response_under_weaker_grounding"
    assert profile["action_preferences"][0]["purpose_anchor"] == "bounded_response_under_constraint"
    assert profile["deeper_walk_hint"] == "keep response concise unless stronger grounding signals appear"
    assert learned_profile["traceability"]["source_patterns"][0]["pattern"]["grounding_success"] is False
    profile_snapshot = snapshot["readable_profile_snapshot"]
    assert profile_snapshot["identity_assurance_posture"]["level"] == "weak"
    assert profile_snapshot["principal_readable_boundary"]["resolved_constitution_context"]["present"] is False
    assert profile_snapshot["principal_readable_boundary"]["profile_claims"][1]["field"] == "productive_coord_family"
    assert (
        profile_snapshot["principal_readable_boundary"]["profile_claims"][1]["summary"]["decision_basis"]
        == "concise_response_under_weaker_grounding"
    )
    assert (
        profile_snapshot["principal_readable_boundary"]["profile_claims"][1]["summary"]["governing_basis"]
        == "allow:baseline_satisfied"
    )
    self_profile = snapshot["aggregate_agent_self_profile"]
    assert self_profile["source"]["derived_from"] == "learned_autonomy_profile"
    assert self_profile["source"]["source_pattern_count"] == 1


def test_readable_profile_snapshot_distinguishes_resolved_constitution_context_from_runtime_identity() -> None:
    snapshot = _diagnostics_snapshot(
        {
            "ledger_id": "chat-demo",
            "resolve_summary": {
                "requested_count": 1,
                "resolved_count": 1,
                "resolved": ["chat-demo:WX-42"],
                "unresolved_count": 0,
            },
            "epistemic_status": {
                "method": "direct_decode",
                "opened_payload_coords": ["chat-demo:WX-42"],
                "source_coords": ["chat-demo:WX-42"],
            },
            "runtime_identity": {
                "library_boundary": {
                    "canonical_ledger_id": "chat-demo",
                    "hot_path_mode": "summary_only",
                    "foundation_identity": {
                        "name": None,
                        "purpose": None,
                        "source": None,
                    },
                }
            },
        }
    )

    profile_snapshot = snapshot["readable_profile_snapshot"]
    principal_boundary = profile_snapshot["principal_readable_boundary"]
    assert principal_boundary["resolved_constitution_context"]["present"] is True
    assert principal_boundary["resolved_constitution_context"]["resolved_preview"] == ["chat-demo:WX-42"]
    assert principal_boundary["resolved_constitution_context"]["source_coords"] == ["chat-demo:WX-42"]
    assert principal_boundary["resolved_constitution_context"]["opened_payload_coords"] == ["chat-demo:WX-42"]
    assert principal_boundary["resolved_constitution_context"]["direct_decode_observed"] is True
    assert principal_boundary["runtime_foundation_identity"]["available"] is False
    assert principal_boundary["runtime_foundation_identity"]["fields"] == {
        "name": None,
        "purpose": None,
        "source": None,
    }
    self_profile = snapshot["aggregate_agent_self_profile"]
    assert self_profile["source"]["resolved_constitution_context_present"] is True
    assert self_profile["source"]["runtime_foundation_identity_available"] is False


def test_between_turn_enrichment_distinguishes_resolved_context_from_foundation_identity_rehydration() -> None:
    snapshot = _diagnostics_snapshot(
        {
            "ledger_id": "chat-demo",
            "resolve_summary": {
                "requested_count": 1,
                "resolved_count": 1,
                "resolved": ["chat-demo:WX-42"],
                "unresolved_count": 0,
            },
            "epistemic_status": {
                "method": "direct_decode",
                "opened_payload_coords": ["chat-demo:WX-42"],
                "source_coords": ["chat-demo:WX-42"],
            },
            "runtime_identity": {
                "library_boundary": {
                    "canonical_ledger_id": "chat-demo",
                    "hot_path_mode": "summary_only",
                    "latency_boundary": {"deep_history_requires_fallback_or_deferral": True},
                    "foundation_identity": {
                        "name": None,
                        "purpose": None,
                        "source": None,
                    },
                }
            },
        }
    )

    enrichment = snapshot["between_turn_enrichment"]
    assert enrichment["prior_payload_context"]["attributable_resolved_context_present"] is True
    assert enrichment["prior_payload_context"]["resolved_preview"] == ["chat-demo:WX-42"]
    assert enrichment["prior_payload_context"]["source_coords"] == ["chat-demo:WX-42"]
    assert enrichment["prior_payload_context"]["opened_payload_coords"] == ["chat-demo:WX-42"]
    assert enrichment["prior_payload_context"]["direct_decode_observed"] is True
    assert enrichment["prior_payload_context"]["resolved_context_is_not_foundation_identity_rehydration"] is True
    assert enrichment["foundation_identity_rehydration"]["available"] is False
    assert enrichment["foundation_identity_rehydration"]["fields"] == {
        "name": None,
        "purpose": None,
        "source": None,
    }
