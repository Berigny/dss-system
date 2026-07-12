from __future__ import annotations

from backend.api.chat import (
    _autonomy_decision_from_candidates,
    _autonomy_system_instruction,
    _candidate_trace_from_retrieved,
)


def test_candidate_trace_from_retrieved_orders_by_relevance() -> None:
    retrieved = [
        {"coord": "chat-demo:WX-1", "relevance_score": 0.4, "tier_rank": 1},
        {"coord": "chat-demo:WX-2", "relevance_score": 0.9, "tier_rank": 3, "resolved_payload_present": True},
    ]
    trace = _candidate_trace_from_retrieved(retrieved)
    assert trace[0]["coord"] == "chat-demo:WX-2"
    assert trace[0]["tier_rank"] == 3


def test_balanced_policy_prefers_resolve_for_tier3_resolved() -> None:
    decision = _autonomy_decision_from_candidates(
        [
            {
                "coord": "chat-demo:WX-9",
                "relevance_score": 0.92,
                "tier_rank": 3,
                "resolved_payload_present": True,
                "source": "retrieved",
            }
        ],
        policy="balanced",
    )
    assert decision["action"] == "resolve"
    assert "tier3_resolved" in str(decision["reason"])


def test_non_balanced_policy_falls_back_to_balanced() -> None:
    decision = _autonomy_decision_from_candidates(
        [{"coord": "chat-demo:WX-7", "relevance_score": 0.5, "tier_rank": 1, "source": "recent"}],
        policy="legacy",
    )
    assert decision["policy"] == "balanced"
    assert decision["action"] in {"resolve", "reuse_path", "answer_from_priors"}


def test_weak_four_candidates_request_new_set() -> None:
    decision = _autonomy_decision_from_candidates(
        [
            {"coord": "chat-demo:WX-1", "relevance_score": 0.1, "tier_rank": 0, "source": "retrieved"},
            {"coord": "chat-demo:WX-2", "relevance_score": 0.12, "tier_rank": 0, "source": "retrieved"},
            {"coord": "chat-demo:WX-3", "relevance_score": 0.08, "tier_rank": 0, "source": "retrieved"},
            {"coord": "chat-demo:WX-4", "relevance_score": 0.09, "tier_rank": 0, "source": "retrieved"},
        ],
        policy="balanced",
    )
    assert decision["action"] == "request_new_candidate_set"
    assert decision["reason"] == "top_four_candidates_not_useful"


def test_autonomy_instruction_mentions_selected_coord() -> None:
    text = _autonomy_system_instruction({"action": "resolve", "chosen_coord": "chat-demo:WX-44"})
    assert "chat-demo:WX-44" in text
