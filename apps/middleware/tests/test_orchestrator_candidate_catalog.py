"""Tests for DSS-134: compact four-tier COORD candidate catalog."""

import pytest

from routes.orchestrator import (
    _build_candidate_trace,
    _build_branch_selection_summary,
    _candidate_p_adic_score,
    _candidate_search_score,
    _candidate_relevance_tier,
    _candidate_origin_eligibility,
    _candidate_skip_reason,
    _candidate_recommended_action,
)


class TestCandidateScores:
    def test_p_adic_score_from_p_adic_similarity(self):
        assert _candidate_p_adic_score({"p_adic_similarity": 0.75}) == 0.75

    def test_p_adic_score_from_ancestry_score(self):
        assert _candidate_p_adic_score({"ancestry_score": 0.6}) == 0.6

    def test_p_adic_score_fallback_to_zero(self):
        assert _candidate_p_adic_score({}) == 0.0

    def test_search_score_from_search_score(self):
        assert _candidate_search_score({"search_score": 0.8}) == 0.8

    def test_search_score_from_relevance_score(self):
        assert _candidate_search_score({"relevance_score": 0.7}) == 0.7


class TestRelevanceTier:
    def test_explicit_user_referenced_is_tier_1(self):
        assert _candidate_relevance_tier(
            {}, origin_attestation="explicit_user_referenced_coord",
            p_adic_score=0.0, search_score=0.0, recency_score=0.0
        ) == 1

    def test_user_attachment_parent_is_tier_2(self):
        assert _candidate_relevance_tier(
            {}, origin_attestation="user_attachment_parent",
            p_adic_score=0.0, search_score=0.0, recency_score=0.0
        ) == 2

    def test_model_response_wx_is_tier_4(self):
        assert _candidate_relevance_tier(
            {}, origin_attestation="model_response_wx",
            p_adic_score=0.9, search_score=0.9, recency_score=0.9
        ) == 4

    def test_strong_signal_is_tier_3(self):
        assert _candidate_relevance_tier(
            {}, origin_attestation="user_message",
            p_adic_score=0.7, search_score=0.0, recency_score=0.0
        ) == 3

    def test_weak_signal_is_tier_4(self):
        assert _candidate_relevance_tier(
            {}, origin_attestation="user_message",
            p_adic_score=0.1, search_score=0.1, recency_score=0.1
        ) == 4


class TestOriginEligibility:
    def test_tier_1_and_2_are_fully_eligible(self):
        assert _candidate_origin_eligibility("explicit_user_referenced_coord", 1) == 1.0
        assert _candidate_origin_eligibility("user_attachment_parent", 2) == 1.0

    def test_model_response_wx_is_demoted(self):
        assert _candidate_origin_eligibility("model_response_wx", 4) == 0.25

    def test_user_message_tier_3_is_half(self):
        assert _candidate_origin_eligibility("user_message", 3) == 0.5


class TestSkipReason:
    def test_wx_without_explicit_gets_demoted_reason(self):
        reason = _candidate_skip_reason(
            {}, origin_attestation="model_response_wx",
            relevance_tier=4, p_adic_score=0.0, search_score=0.0, recency_score=0.0
        )
        assert reason == "assistant_output_demoted_to_continuity_lane"

    def test_wx_with_explicit_no_skip(self):
        # When explicit=True, relevance_tier is 1 in practice, so neither
        # demotion nor insufficient-signal skip reasons apply.
        reason = _candidate_skip_reason(
            {"explicit": True}, origin_attestation="model_response_wx",
            relevance_tier=1, p_adic_score=0.0, search_score=0.0, recency_score=0.0
        )
        assert reason is None

    def test_weak_signal_tier_4_gets_insufficient_signal(self):
        reason = _candidate_skip_reason(
            {}, origin_attestation="user_message",
            relevance_tier=4, p_adic_score=0.1, search_score=0.1, recency_score=0.1
        )
        assert reason == "insufficient_p_adic_search_recency_signal"


class TestRecommendedAction:
    def test_tier_1_already_opened_is_reuse(self):
        action = _candidate_recommended_action(
            {}, payload_state="already_opened_in_session", origin_attestation="explicit_user_referenced_coord",
            coord_type="WX", relevance_tier=1, skip_reason=None
        )
        assert action == "reuse_already_opened"

    def test_tier_1_opened_is_open(self):
        action = _candidate_recommended_action(
            {}, payload_state="opened", origin_attestation="explicit_user_referenced_coord",
            coord_type="WX", relevance_tier=1, skip_reason=None
        )
        assert action == "open"

    def test_tier_1_not_opened_is_open(self):
        action = _candidate_recommended_action(
            {}, payload_state="sealed", origin_attestation="explicit_user_referenced_coord",
            coord_type="WX", relevance_tier=1, skip_reason=None
        )
        assert action == "open"

    def test_demoted_wx_is_walk(self):
        action = _candidate_recommended_action(
            {}, payload_state="opened", origin_attestation="model_response_wx",
            coord_type="WX", relevance_tier=4,
            skip_reason="assistant_output_demoted_to_continuity_lane"
        )
        assert action == "walk_referenced_coord"

    def test_insufficient_signal_is_skip(self):
        action = _candidate_recommended_action(
            {}, payload_state="sealed", origin_attestation="user_message",
            coord_type="WX", relevance_tier=4,
            skip_reason="insufficient_p_adic_search_recency_signal"
        )
        assert action == "skip"


class TestBuildCandidateTrace:
    def test_default_limit_is_four(self):
        items = [
            {"coord": f"chat-demo:WX-{i:04d}", "relevance_score": 0.9 - (i * 0.01)}
            for i in range(10)
        ]
        trace = _build_candidate_trace(items)
        assert len(trace) == 4

    def test_relevance_tier_present(self):
        items = [{"coord": "chat-demo:WX-0001", "relevance_score": 0.9, "explicit": True}]
        trace = _build_candidate_trace(items)
        assert trace[0]["relevance_tier"] == 1

    def test_p_adic_score_present(self):
        items = [{"coord": "chat-demo:WX-0001", "relevance_score": 0.9, "p_adic_similarity": 0.75}]
        trace = _build_candidate_trace(items)
        assert trace[0]["p_adic_score"] == 0.75

    def test_search_score_present(self):
        items = [{"coord": "chat-demo:WX-0001", "relevance_score": 0.9, "search_score": 0.8}]
        trace = _build_candidate_trace(items)
        assert trace[0]["search_score"] == 0.8

    def test_skip_reason_for_demoted_wx(self):
        items = [{"coord": "chat-demo:WX-0001", "relevance_score": 0.9, "role": "assistant"}]
        trace = _build_candidate_trace(items)
        assert trace[0]["skip_reason"] == "assistant_output_demoted_to_continuity_lane"

    def test_origin_attestation_present(self):
        items = [{"coord": "chat-demo:WX-0001", "relevance_score": 0.9, "explicit": True}]
        trace = _build_candidate_trace(items)
        assert trace[0]["origin_attestation"] == "explicit_user_referenced_coord"

    def test_wx_coords_sorted_after_explicit(self):
        items = [
            {"coord": "chat-demo:WX-0001", "relevance_score": 0.5, "role": "assistant"},
            {"coord": "chat-demo:WX-0002", "relevance_score": 0.9, "explicit": True},
        ]
        trace = _build_candidate_trace(items)
        assert trace[0]["coord"] == "chat-demo:WX-0002"
        assert trace[1]["coord"] == "chat-demo:WX-0001"


class TestBuildBranchSelectionSummary:
    def test_new_fields_included(self):
        trace = [
            {
                "coord": "chat-demo:WX-0001",
                "relevance_score": 0.9,
                "origin_attestation": "explicit_user_referenced_coord",
                "origin_eligibility": 1.0,
                "relevance_tier": 1,
                "p_adic_score": 0.75,
                "search_score": 0.8,
                "recency_score": 0.6,
                "payload_state": "opened",
                "recommended_action": "reuse_already_opened",
                "skip_reason": None,
                "resolved_payload_present": True,
            }
        ]
        summary = _build_branch_selection_summary(trace)
        row = summary["candidate_coords_considered"][0]
        assert row["relevance_tier"] == 1
        assert row["p_adic_score"] == 0.75
        assert row["search_score"] == 0.8
        assert row["origin_eligibility"] == 1.0
        assert row["recommended_action"] == "reuse_already_opened"
        assert row["skip_reason"] is None
