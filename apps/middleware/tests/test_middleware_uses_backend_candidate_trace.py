"""Tests for HENGE-007: middleware consumes canonical backend candidate_trace."""

from __future__ import annotations

from routes.orchestrator import _autonomy_decision_from_trace, _build_candidate_trace


def test_build_candidate_trace_prefers_backend_trace() -> None:
    backend_trace = [
        {
            "coord": "chat-demo:WX-0001",
            "coord_type": "WX",
            "origin_attestation": "explicit_user_referenced_coord",
            "origin_eligibility": 1.0,
            "relevance_tier": 1,
            "relevance_score": 0.95,
            "tier_rank": 3,
            "p_adic_score": 0.88,
            "search_score": 0.9,
            "recency_score": 0.7,
            "payload_state": "sealed",
            "recommended_action": "open",
            "skip_reason": None,
            "resolved_payload_present": True,
            "source": "explicit",
        }
    ]
    assemble_result = {
        "candidate_trace": backend_trace,
        "retrieved": [{"coord": "chat-demo:WX-0001", "p_adic_distance": 0.123, "p_adic_norm": 0.456}],
    }

    trace = _build_candidate_trace(assemble_result, limit=4)
    assert len(trace) == 1
    row = trace[0]
    # Tiering is taken from the backend without recomputation.
    assert row["relevance_tier"] == 1
    assert row["tier_rank"] == 3
    assert row["relevance_score"] == 0.95
    # Transport-level enrichment from the raw retrieved item is merged.
    assert row["p_adic_distance"] == 0.123
    assert row["p_adic_norm"] == 0.456
    # Session payload state is recomputed (no opened coords here -> sealed).
    assert row["payload_state"] == "sealed"
    assert row["payload_loaded"] is True


def test_build_candidate_trace_falls_back_to_local_logic() -> None:
    items = [
        {"coord": "chat-demo:WX-0001", "relevance_score": 0.9, "explicit": True},
    ]
    trace = _build_candidate_trace(items)
    assert trace[0]["relevance_tier"] == 1
    assert trace[0]["origin_attestation"] == "explicit_user_referenced_coord"


def test_autonomy_decision_prefers_backend_decision() -> None:
    backend_decision = {
        "policy": "balanced",
        "action": "resolve",
        "reason": "backend_resolved",
        "chosen_coord": "chat-demo:WX-0001",
        "top_k": [],
        "utility": {"resolve": 1.0, "reuse_path": 0.0, "answer_from_priors": 0.0},
    }
    assemble_result = {"autonomy_decision": backend_decision}
    decision = _autonomy_decision_from_trace([], "balanced", assemble_result=assemble_result)
    assert decision == backend_decision


def test_autonomy_decision_computes_locally_when_backend_missing() -> None:
    trace = [
        {
            "coord": "chat-demo:WX-0001",
            "relevance_score": 0.9,
            "tier_rank": 3,
            "resolved_payload_present": True,
            "source": "retrieved",
            "payload_state": "sealed",
        }
    ]
    decision = _autonomy_decision_from_trace(trace, "balanced")
    assert decision["action"] == "resolve"
    assert decision["reason"] == "top_candidate_tier3_resolved"
