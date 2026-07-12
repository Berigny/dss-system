from __future__ import annotations

from datetime import datetime, timezone

from backend.fieldx_kernel.orchestrator import _score_candidate_relevance


def test_score_candidate_relevance_uses_existing_score_and_tier() -> None:
    now = datetime(2026, 3, 3, tzinfo=timezone.utc)
    item = {"relevance_score": 0.88, "tier_rank": 3}
    score, tier = _score_candidate_relevance(
        item,
        query_intent="respond",
        now=now,
    )
    assert score == 0.88
    assert tier == 3


def test_score_candidate_relevance_recent_floor_applies() -> None:
    now = datetime(2026, 3, 3, tzinfo=timezone.utc)
    item = {
        "created_at": now.isoformat(),
        "state": {"metadata": {"kind": "chat", "teleology_alignment": 0.0}},
    }
    score, tier = _score_candidate_relevance(
        item,
        query_intent="respond",
        now=now,
        from_recent=True,
    )
    assert score >= 0.35
    assert tier >= 1


def test_score_candidate_relevance_history_queries_promote_recent_chat() -> None:
    now = datetime(2026, 3, 3, tzinfo=timezone.utc)
    item = {
        "created_at": "2026-03-01T00:00:00+00:00",
        "state": {"metadata": {"kind": "chat", "teleology_alignment": 0.0}},
    }
    score, tier = _score_candidate_relevance(
        item,
        query_intent="history",
        now=now,
        from_recent=True,
    )
    assert score >= 0.45
    assert tier >= 1
